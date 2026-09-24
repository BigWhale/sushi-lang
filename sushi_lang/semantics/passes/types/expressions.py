"""Expression validation for type validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, NamedTuple, Optional, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.semantics import array_runs
from sushi_lang.semantics.typesys import BuiltinType, ArrayType, DynamicArrayType, EnumType, StructType
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ast import ArrayLiteral, IndexAccess, CastExpr, TryExpr, BinaryOp, UnaryOp, Expr, RangeExpr, MemberAccess
from sushi_lang.semantics.type_predicates import (
    BUILTIN_INTEGER_TYPES, is_integer_type, is_numeric_type)
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from .compatibility import is_valid_cast
from .utils import validate_constant_array_index
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.semantics.typesys import Type


def validate_array_literal(validator: 'TypeValidator', expr: ArrayLiteral) -> None:
    """Validate array literal - all elements must have same type."""
    if not expr.elements:
        return

    for element in expr.elements:
        validator.validate_expression(element.value)
        if element.count is not None:
            validator.validate_expression(element.count)

    # CE2017 for a repeat count that is not a count, CE2019 for a range that yields nothing,
    # and CE2020 for a range carrying a count. A `const` never arrives here with one, because
    # its evaluator reads the same runs first and `validate_constant` returns on the error --
    # so the two speakers cannot both fire for one literal. The result is discarded: this
    # call is here to SPEAK, and every caller that needs the runs reads them itself.
    array_runs.read_runs(
        expr.elements,
        array_runs.const_int_reader(validator.constant_evaluator()),
        validator.reporter)

    # Check type consistency of all elements (CE2013). A range element compares as the i32
    # it puts in a slot, and not as the Iterator@(i32) it types as (Ruling 4, #478).
    from sushi_lang.semantics.passes.types.inference import infer_array_element_type

    first_element_type = infer_array_element_type(validator, expr.elements[0].value)
    if first_element_type is not None:
        for element in expr.elements[1:]:
            element_type = infer_array_element_type(validator, element.value)
            if element_type is not None and element_type != first_element_type:
                er.emit(validator.reporter, er.ERR.CE2013, element.value.loc,
                       expected=display_type(first_element_type), got=display_type(element_type))


def validate_index_access(validator: 'TypeValidator', expr: IndexAccess) -> None:
    """Validate array indexing - array must be array type, index must be int."""
    validator.validate_expression(expr.array)

    validator.validate_expression(expr.index)

    index_type = validator.infer_expression_type(expr.index)
    if index_type is not None and index_type != BuiltinType.I32:
        er.emit(validator.reporter, er.ERR.CE2002, expr.index.loc,
               got=display_type(index_type), expected=display_type(BuiltinType.I32))

    array_type = validator.infer_expression_type(expr.array)
    if array_type is not None and not isinstance(array_type, (ArrayType, DynamicArrayType)):
        er.emit(validator.reporter, er.ERR.CE2002, expr.array.loc,
               got=display_type(array_type), expected="array type")

    if isinstance(array_type, ArrayType):
        validate_constant_array_index(expr.index, array_type.size, validator.reporter)


def validate_cast_expression(validator: 'TypeValidator', expr: CastExpr) -> None:
    """Validate a cast expression and check if the cast is valid."""
    # An integer literal (or negated literal) cast directly to an integer type
    # materializes at the TARGET width, so it is exempt
    # from the bare-literal i32 range check (CE2070). Mark it before recursing.
    from sushi_lang.semantics.ast import IntLit, UnaryOp
    if expr.target_type in BUILTIN_INTEGER_TYPES:
        if isinstance(expr.expr, IntLit):
            expr.expr.in_cast_context = True
        elif (isinstance(expr.expr, UnaryOp) and expr.expr.op == "neg"
              and isinstance(expr.expr.expr, IntLit)):
            expr.expr.expr.in_cast_context = True

    validator.validate_expression(expr.expr)

    source_type = validator.infer_expression_type(expr.expr)
    target_type = expr.target_type

    # Stamp the operand's semantic type for the backend (signedness of the cast
    # opcode is unrecoverable from the signless LLVM type). Follows the
    # resolved_* annotation pattern.
    expr.source_type = source_type

    if source_type is None:
        return

    if not is_valid_cast(source_type, target_type):
        er.emit(validator.reporter, er.ERR.CE2014, expr.loc,
               source=display_type(source_type), target=display_type(target_type))


def validate_range_expression(validator: 'TypeValidator', expr: 'RangeExpr') -> None:
    """Validate range expression - start and end must be integer types."""
    validator.validate_expression(expr.start)
    start_type = validator.infer_expression_type(expr.start)

    validator.validate_expression(expr.end)
    end_type = validator.infer_expression_type(expr.end)

    if start_type is not None and not is_numeric_type(start_type):
        er.emit(validator.reporter, er.ERR.CE2072, expr.start.loc,
               got=display_type(start_type), expected="integer type")

    if end_type is not None and not is_numeric_type(end_type):
        er.emit(validator.reporter, er.ERR.CE2072, expr.end.loc,
               got=display_type(end_type), expected="integer type")

    # Note: We accept any integer type (i8, i16, i32, i64, u8, u16, u32, u64)
    # but the backend will cast to i32 for iteration. Type compatibility
    # checking happens during cast emission.


class _Arms(NamedTuple):
    """What a `??` reads off the wrapper it is applied to."""
    value_type: Optional['Type']       # the payload the success arm carries
    success_tag: Optional[int]         # that arm's variant index
    error_type: Optional['Type']       # the failure arm's payload, if it has one
    error_tag: Optional[int]           # that arm's variant index


_NO_ARMS = _Arms(None, None, None, None)


def validate_try_expression(validator: 'TypeValidator', expr: 'TryExpr') -> None:
    """Validate ?? operator usage and annotate AST with inferred types.

    Four questions, in this order, and each one reports its own fault: what the wrapper
    under the `??` carries (CE2507), what channel encloses it (CE2508), whether the two
    failure arms name one type (CE2511), and what the back end reads off the node.
    """
    validator.validate_expression(expr.expr)

    inner_type = validator.infer_expression_type(expr.expr)
    arms = _unwrapped_arms(validator, expr, inner_type)
    if arms is None:
        return

    channel = _enclosing_channel(validator, expr)
    if channel is None:
        return

    if not _error_arms_agree(validator, expr, inner_type, channel):
        return

    _annotate_try_expr(expr, inner_type, arms, channel)


def _unwrapped_arms(validator: 'TypeValidator', expr: 'TryExpr',
                    inner_type: Optional['Type']) -> Optional[_Arms]:
    """What the wrapper under a `??` carries, or None once CE2507 is reported.

    An uninferrable operand carries nothing and reports nothing: the fault that made it
    uninferrable has a diagnostic of its own already.
    """
    if inner_type is None:
        return _NO_ARMS

    if not isinstance(inner_type, EnumType):
        er.emit(validator.reporter, er.ERR.CE2507, expr.loc, got=display_type(inner_type))
        return None

    ok_variant = inner_type.get_variant("Ok")
    err_variant = inner_type.get_variant("Err")
    if ok_variant and err_variant and len(ok_variant.associated_types) == 1:
        return _Arms(
            value_type=ok_variant.associated_types[0],
            success_tag=inner_type.get_variant_index("Ok"),
            error_type=(err_variant.associated_types[0]
                        if err_variant.associated_types else None),
            error_tag=inner_type.get_variant_index("Err"),
        )

    # A Maybe-like wrapper has no failure payload: `??` still propagates, as an Err.
    some_variant = inner_type.get_variant("Some")
    none_variant = inner_type.get_variant("None")
    if (some_variant and none_variant and len(some_variant.associated_types) == 1
            and len(none_variant.associated_types) == 0):
        return _Arms(
            value_type=some_variant.associated_types[0],
            success_tag=inner_type.get_variant_index("Some"),
            error_type=None,
            error_tag=None,
        )

    er.emit(validator.reporter, er.ERR.CE2507, expr.loc, got=display_type(inner_type))
    return None


def _enclosing_channel(validator: 'TypeValidator', expr: 'TryExpr') -> Optional['Type']:
    """The interned `Result@(T, E)` a `??` propagates into, or None once it is reported.

    Every way of having no channel reads CE2508, and there is one silent way out: a BARE
    extension body has no channel and the collect pass already rejected every `??` in it
    with CE0131 (#398), so a second diagnostic here would only mislead.
    """
    declared = _declared_channel(validator, expr)
    if declared is None:
        return None

    return _intern_channel(validator, expr, declared)


def _declared_channel(validator: 'TypeValidator', expr: 'TryExpr') -> Optional['Type']:
    """The return type the enclosing body declares, before it is interned."""
    if validator.current_function is None:
        # A CHANNEL extension body (`| E`, ruling 1) propagates into its own interned
        # Result. A BARE one is the silent case above; any other None context keeps the
        # CE2508 backstop.
        channel = getattr(validator, "extension_channel_result", None)
        if channel is None:
            if not getattr(validator, "in_extension_context", False):
                er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
            return None
        return channel

    # CW2511: `??` works in main, but explicit handling is clearer at the entry point.
    if validator.current_function.name == "main":
        er.emit(validator.reporter, er.ERR.CW2511, expr.loc)

    if validator.current_function.ret is None:
        er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
        return None
    return validator.current_function.ret


def _intern_channel(validator: 'TypeValidator', expr: 'TryExpr',
                    declared: 'Type') -> Optional['Type']:
    """The declared channel as its interned Result, whichever way it was spelled.

    An implicit `fn foo() T` / `fn foo() T | E`, an explicit `fn foo() Result@(T, E)`
    still spelled as a `GenericTypeRef`, or a signature already resolved in place.
    """
    from sushi_lang.semantics.generics.results import (
        ensure_result_type_in_table, is_result_enum,
    )

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name

    def intern(ok: 'Type', err: 'Type'):
        return ensure_result_type_in_table(validator.enum_table, ok, err,
                                           struct_table=structs)

    # Result ALONE, where `utils.intern_declared_wrapper` also answers for a written
    # `Maybe@(T)`: a Maybe return type belongs in the wrap below, as the OK payload of
    # the enclosing Result, and interning it here would make `??` read it as the channel.
    if isinstance(declared, GenericTypeRef) and declared.base_name == "Result":
        if len(declared.type_args) != 2:
            er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
            return None
        declared = intern(declared.type_args[0], declared.type_args[1])
    elif not is_result_enum(declared):
        if (validator.current_function is not None
                and validator.current_function.err_type is not None):
            err_type = resolve_unknown_type(
                validator.current_function.err_type, structs, enums)
        else:
            err_type = enums.get("StdError")
        if err_type is None:
            er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
            return None
        declared = intern(declared, err_type)

    if not is_result_enum(declared):
        er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
        return None
    return declared


def _error_arms_agree(validator: 'TypeValidator', expr: 'TryExpr',
                      inner_type: Optional['Type'], channel: 'Type') -> bool:
    """Whether the wrapper's failure arm names the channel's, or CE2511.

    Strict matching with no conversion. The two sides used to be compared as STRINGS,
    because one Result had many instances; both are interned now, so they compare as
    types.
    """
    from sushi_lang.semantics.generics.results import result_ok_err

    outer_ok_type, outer_err_type = result_ok_err(channel)

    inner_err_type = None
    if isinstance(inner_type, EnumType):
        err_variant = inner_type.get_variant("Err")
        if err_variant and err_variant.associated_types:
            inner_err_type = err_variant.associated_types[0]

    if inner_err_type is None or outer_err_type is None:
        return True

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    if (resolve_unknown_type(inner_err_type, structs, enums)
            == resolve_unknown_type(outer_err_type, structs, enums)):
        return True

    er.emit(validator.reporter, er.ERR.CE2511, expr.loc,
            ok_type=display_type(outer_ok_type),
            inner_err=display_type(inner_err_type),
            outer_err=display_type(outer_err_type))
    return False


def _annotate_try_expr(expr: 'TryExpr', inner_type: Optional['Type'],
                       arms: _Arms, channel: 'Type') -> None:
    """Annotate TryExpr AST node with inferred type information."""
    expr.inferred_inner_type = inner_type
    expr.inferred_unwrapped_type = arms.value_type
    expr.inferred_success_tag = arms.success_tag
    expr.inferred_error_type = arms.error_type
    expr.inferred_error_tag = arms.error_tag
    expr.inferred_func_return_type = channel


def reject_mixed_numeric_operands(validator: 'TypeValidator', expr: BinaryOp,
                                  left_type: 'Optional[Type]',
                                  right_type: 'Optional[Type]') -> None:
    """CE2510 when two numeric operands are not of the same type.

    One rule for every operator that takes its result from operands which must
    already agree: arithmetic, comparison, and the bitwise `& | ^`. Sushi converts
    no numeric type implicitly, so the operands say what the result is and `as` is
    the only way to change a width. A shift never asks both its operands: the
    count says how far to move, not what the result is.
    """
    if left_type is None or right_type is None:
        return
    if not (is_numeric_type(left_type) and is_numeric_type(right_type)):
        return
    if left_type == right_type:
        return

    er.emit(validator.reporter, er.ERR.CE2510, expr.loc,
            left_type=display_type(left_type), right_type=display_type(right_type))


_EQUALITY_OPS = ("==", "!=")

# The non-numeric types each operator group accepts. A numeric pair never reaches
# these sets: it belongs to CE2510, which says which two widths met. Both sets are
# closed, so a type kind nobody thought about is a diagnostic and not a crash.
_EQUALITY_NON_NUMERIC = frozenset({BuiltinType.BOOL, BuiltinType.STRING})
_ORDER_NON_NUMERIC = frozenset({BuiltinType.STRING})

# Arithmetic accepts no non-numeric type at all, so its set needs no name. The
# operators it covers do: the unary minus arrives as '-' and not as 'neg'.
ARITHMETIC_OPS = ("+", "-", "*", "/", "%")
DIVISION_OPS = ("/", "%")
COMPARISON_OPS = _EQUALITY_OPS + ("<", "<=", ">", ">=")


def is_string_plus(op: str, left_type: 'Optional[Type]',
                   right_type: 'Optional[Type]') -> bool:
    """Is this the '+' that CE2509 owns -- the one with a string operand?

    Sushi has no concatenation operator, so CE2509 names the interpolation that
    replaces it. The arithmetic operand rule steps over exactly this pair, because
    one fault reads better as one diagnostic.
    """
    return op == "+" and BuiltinType.STRING in (left_type, right_type)


def reject_non_numeric_arithmetic(validator: 'TypeValidator', op: str,
                                  operands: 'list[Tuple[Expr, Optional[Type]]]') -> None:
    """CE2518 for the first arithmetic operand that is not a number.

    One rule for the whole group: + - * / % and the unary minus, which is why the
    operands arrive as a list rather than as a pair. Arithmetic combines numbers and
    Sushi converts nothing on its own, so the permitted set is exactly the numeric
    types and there is no non-numeric escape to name -- where a comparison needs two
    allow-lists, this needs none.

    The first bad operand is enough: `a - b` on two strings is one fault. A wrapper
    is told how to take its value out, because a missing '??' is the common way here.

    Before #709 the pass asked nothing, so every operand reached the backend: a bool
    or a wrapper became a CE0000 out of `emit_arithmetic`, a struct, an enum or an
    array failed the LLVM IR parse, and `-true` compiled and printed `true`.
    """
    if len(operands) == 2 and is_string_plus(op, operands[0][1], operands[1][1]):
        return

    for operand, operand_type in operands:
        if operand_type is None or is_numeric_type(operand_type):
            continue
        report = er.emit_with(validator.reporter, er.ERR.CE2518, operand.loc,
                              op=op, type_name=display_type(operand_type))
        if _wrapper_of(operand_type) is not None:
            report = report.help("take the value with '??', '.realise(default)' "
                                 "or match")
        report.emit()
        return


def reject_zero_divisor(validator: 'TypeValidator', expr: BinaryOp,
                        left_type: 'Optional[Type]') -> None:
    """CE0112 when the compiler can read the divisor of '/' or '%' and it is zero.

    The same code the constant evaluator emits, because it is the same rule: one
    compile-time arithmetic, and a body cannot disagree with a constant. A divisor
    the compiler cannot read is ordinary code and is left alone, exactly as a
    computed shift count is (CE2512).

    A left operand that is not numeric belongs to CE2518, which has already spoken;
    reporting a zero divisor as well would make one expression two faults.
    """
    from sushi_lang.semantics.const_eval import is_numeric_constant

    if left_type is None or not is_numeric_type(left_type):
        return

    divisor = validator.constant_evaluator().evaluate(expr.right, left_type, None)
    if divisor is None or not is_numeric_constant(divisor) or divisor.value != 0:
        return

    er.emit(validator.reporter, er.ERR.CE0112, expr.right.loc)


def has_builtin_equality(ty: 'Type') -> bool:
    """Can two values of `ty` meet `==`? THE closed equality set, in one place.

    `reject_uncomparable_operands` below reads the sets directly for the operators;
    the array search methods (`contains`, `index_of`) ask through this predicate, so
    the two rules cannot drift apart (CE2100 cites CE2514 for a reason).
    """
    return is_numeric_type(ty) or ty in _EQUALITY_NON_NUMERIC


def _comparison_escape(ty: 'Type') -> Optional[str]:
    """The way to ask the question that a comparison of this type cannot answer."""
    if isinstance(ty, EnumType):
        return "use match to ask which variant the value holds"
    if ty == BuiltinType.BOOL:
        return "use != to ask whether two bools differ"
    if isinstance(ty, (ArrayType, DynamicArrayType)):
        return "compare the elements, or the lengths"
    if isinstance(ty, StructType):
        return "compare the fields one at a time"
    return None


def reject_uncomparable_operands(validator: 'TypeValidator', expr: BinaryOp,
                                 left_type: 'Optional[Type]',
                                 right_type: 'Optional[Type]') -> None:
    """CE2513 for a mixed pair, CE2514 for a type that carries no such comparison.

    One rule for all six comparison operators. Equality is defined for the numeric
    types, bool and string; an order is defined for the numeric types and string,
    where it reads the bytes. A bool is deliberately left out of the order, because
    `a < b` on two bools is almost always a typo for `!=`.

    Before #449 the pass asked nothing here, so every other operand pair reached the
    backend, which tried to compare a string, a struct or an array value as an i32
    and answered with a CE0017 internal error.
    """
    if left_type is None or right_type is None:
        return
    if is_numeric_type(left_type) and is_numeric_type(right_type):
        return

    if left_type != right_type:
        er.emit(validator.reporter, er.ERR.CE2513, expr.loc,
                left_type=display_type(left_type),
                right_type=display_type(right_type), op=expr.op)
        return

    permitted = (_EQUALITY_NON_NUMERIC if expr.op in _EQUALITY_OPS
                 else _ORDER_NON_NUMERIC)
    if left_type in permitted:
        return

    builder = er.emit_with(validator.reporter, er.ERR.CE2514, expr.loc,
                           op=expr.op, type_name=display_type(left_type))
    escape = _comparison_escape(left_type)
    if escape is not None:
        builder = builder.help(escape)
    builder.emit()


def validate_bitwise_operation(validator: 'TypeValidator', expr: BinaryOp) -> None:
    """Validate the operands of a bitwise operator: integer, and of one width.

    An integer, not merely a number: a float has no bits to combine, and its bits
    are reached through `.to_bits()`. The gate asked for a numeric type, so a float
    passed it and the backend met an operand LLVM has no such instruction for
    (CE0000, "instruction requires integer or integer vector operands").
    """
    left_type = validator.infer_expression_type(expr.left)
    right_type = validator.infer_expression_type(expr.right)

    if left_type is not None and not is_integer_type(left_type):
        er.emit(validator.reporter, er.ERR.CE2004, expr.left.loc, op=expr.op)
        return

    if right_type is not None and not is_integer_type(right_type):
        er.emit(validator.reporter, er.ERR.CE2004, expr.right.loc, op=expr.op)
        return

    if expr.op in ("&", "|", "^"):
        reject_mixed_numeric_operands(validator, expr, left_type, right_type)

    if expr.op in ("<<", ">>"):
        reject_impossible_shift_count(validator, expr, left_type)


def reject_overflowing_operation(validator: 'TypeValidator', expr: Expr,
                                 result_type: 'Optional[Type]') -> None:
    """CE2077 when an operation the compiler can read gives a value its type cannot hold.

    The evaluator does the reading and the arithmetic, so the language has ONE
    compile-time arithmetic and a constant cannot disagree with the same expression in a
    body. Its reporter is silent here, because an operand that is not constant -- a
    variable, a call -- is ordinary code and not a diagnostic.

    Only an overflow recorded AT THIS node is reported. One recorded deeper belongs to
    the node that computed it: the inner operation of `(200 + 100) / 2` reports once,
    and a constant that overflows is reported where it is declared and not at every use.
    """
    from sushi_lang.semantics.const_eval import emit_overflow

    evaluator = validator.constant_evaluator()
    evaluator.evaluate(expr, result_type, expr.loc)

    overflow = evaluator.overflow
    if overflow is not None and overflow.node is expr:
        emit_overflow(validator.reporter, overflow)


def _provable_shift_count(validator: 'TypeValidator', count: Expr) -> Optional[int]:
    """The count as a number when the compiler can read it, else None.

    A silent reporter is passed in because a count that is not a constant -- a
    variable, a loop index, a call -- is ordinary code and not a diagnostic. Only
    the answer is wanted here, never the evaluator's complaint about not finding
    one.
    """
    from sushi_lang.semantics.const_eval import ScalarConstant

    value = validator.constant_evaluator().evaluate(count, BuiltinType.I64, None)
    if (value is None or not isinstance(value, ScalarConstant)
            or not isinstance(value.value, int) or isinstance(value.value, bool)):
        return None
    return value.value


def reject_impossible_shift_count(validator: 'TypeValidator', expr: BinaryOp,
                                  value_type: 'Optional[Type]') -> None:
    """CE2512 when a shift count the compiler can read cannot fit the value.

    The width of the shifted value is the limit: a count at or above it moves every
    bit out of the type, and a negative count is not a shift. LLVM answers such a
    shift with poison, so nothing reports the loss and the program prints whatever
    the optimizer left behind. A count that cannot be read at compile time is left
    alone -- a bit reader computes its count, and no check is emitted around it.
    """
    from sushi_lang.semantics.integer_width import integer_bit_width

    if value_type is None:
        return
    width = integer_bit_width(value_type)
    if width is None:
        return

    count = _provable_shift_count(validator, expr.right)
    if count is None or 0 <= count < width:
        return

    er.emit(validator.reporter, er.ERR.CE2512, expr.right.loc,
            count=count, value_type=display_type(value_type), max_count=width - 1)


def validate_bitwise_unary(validator: 'TypeValidator', expr: UnaryOp) -> None:
    """Validate that bitwise NOT (~) is used with an integer, floats included out."""
    operand_type = validator.infer_expression_type(expr.expr)

    if operand_type is not None and not is_integer_type(operand_type):
        er.emit(validator.reporter, er.ERR.CE2004, expr.expr.loc, op=expr.op)


# The predicate that turns each wrapper into the bool a condition wants.
_WRAPPER_PREDICATES = {"Result": "is_ok", "Maybe": "is_some"}


def _wrapper_of(ty: Optional['Type']) -> Optional[Tuple[str, str]]:
    """(wrapper name, predicate) when this type is a Result or a Maybe, else None.

    Both spellings answer: the interned enum a resolved signature carries, and the
    GenericTypeRef a declared one keeps.
    """
    for name, predicate in _WRAPPER_PREDICATES.items():
        if isinstance(ty, EnumType) and ty.generic_base == name:
            return name, predicate
        if isinstance(ty, GenericTypeRef) and ty.base_name == name:
            return name, predicate
    return None


def reject_non_bool_condition(validator: 'TypeValidator', expr: Expr,
                              expr_type: Optional['Type'] = None) -> bool:
    """Refuse an expression that stands where a condition belongs and is not a bool.

    The ONE seam for every condition position: an `if`, a `while` (#522), and the
    operands of `and`, `or`, `xor` and `not` (#532). A wrapper is told which predicate
    answers for it (CE2516); everything else is CE2005, which offers the `== 0` escape
    only to an integer -- a string or a struct has no such spelling. Answers True when
    it reported.
    """
    if expr_type is None:
        expr_type = validator.infer_expression_type(expr)

    if expr_type is None or expr_type == BuiltinType.BOOL:
        return False

    wrapper = _wrapper_of(expr_type)
    if wrapper is not None:
        name, predicate = wrapper
        er.emit_with(validator.reporter, er.ERR.CE2516, expr.loc,
                     ty=display_type(expr_type), wrapper=name) \
            .help(f"use '.{predicate}()' to test it, or take the value with "
                  f"'??', '.realise(default)' or match").emit()
        return True

    report = er.emit_with(validator.reporter, er.ERR.CE2005, expr.loc)
    if is_integer_type(expr_type):
        report = report.help("use '== 0' or '!= 0' for integer conditions")
    report.emit()
    return True


def validate_boolean_condition(validator: 'TypeValidator', expr: Expr, context: str) -> None:
    """Validate that a control-flow condition is a bool. Nothing else is one (#522)."""
    validator.validate_expression(expr)
    reject_non_bool_condition(validator, expr)


def _field_names_of(receiver_type: 'Type') -> Optional[list[str]]:
    """The fields a receiver type declares, or None when this position is not ours.

    A STRUCT answers its own list. The kinds below carry no field at all, so each answers
    the empty list and every name behind their dot is a miss. An ENUM is one of them
    (#666): it carries variants, and a variant is reached by a pattern and not by a dot.
    Everything else -- an unresolved name, a generic reference, a receiver the pass could
    not type -- is somebody else's position, and a false CE2106 there would be worse than
    the internal error it replaces.
    """
    from sushi_lang.semantics.typesys import (
        ForeignPtrType, FunctionType, PointerType,
    )

    if isinstance(receiver_type, StructType):
        return [name for name, _ in receiver_type.fields]
    if isinstance(receiver_type, (ArrayType, DynamicArrayType, BuiltinType, EnumType,
                                  FunctionType, PointerType, ForeignPtrType)):
        return []
    return None


def _is_a_method(validator: 'TypeValidator', receiver_type: 'Type', name: str) -> bool:
    """Does the receiver's type carry a METHOD of this name, whoever declares it?

    `builtin_method_exists` is the one seam that knows a compiler-defined method, and the
    extension table holds what the program itself declares. A built-in target takes both.
    """
    from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists

    if validator.extension_table.get_method(receiver_type, name) is not None:
        return True
    return builtin_method_exists(receiver_type, name, validator.derived_methods)


def reject_unknown_field(validator: 'TypeValidator', node: MemberAccess) -> None:
    """A member read wants a field, and a type with no such field is CE2106 (#630, #661).

    The pass used to walk past an unknown field entirely, so the read reached codegen and
    the backend was the first thing to notice -- tier 1, no file and no line, with the
    note that says the fault is a bug in the compiler. It is a typo.

    #630 answered the STRUCT receiver. A receiver that carries NO field -- an array, a
    primitive, a string, a closure, a `ptr` -- still reached the backend, where the shape
    of the read picked the code: CE0031 for a name, CE0044 through a field, CE0043
    through an array element. One rule answers all of them here.

    #661 left the ENUM receiver alone, and that one was worse than an internal error: a
    `Maybe@(T)` is an ordinary interned enum, so the backend unwrapped the receiver to
    its payload struct and read field 0, which is the TAG. The program compiled clean and
    printed a wrong number (#666). An enum is refused here with the rest.
    """
    from sushi_lang.semantics.generics.results import is_builtin_wrapper_enum
    from sushi_lang.semantics.namespaces import suggest_member
    from sushi_lang.semantics.passes.types.visibility import name_is_contested
    from sushi_lang.semantics.typesys import ReferenceType

    if validator.namespace_of(node.receiver) is not None:
        return

    receiver_type = validator.infer_expression_type(node.receiver)
    if isinstance(receiver_type, ReferenceType):
        receiver_type = receiver_type.referenced_type
    if receiver_type is None:
        return

    names = _field_names_of(receiver_type)
    if names is None or node.member in names:
        return
    # A struct name this unit declared and LOST holds the winner's fields, not the
    # unit's own; the loser already heard CE0004 or CE3011 at its declaration (#738).
    if isinstance(receiver_type, StructType) and name_is_contested(
            validator, "struct", receiver_type.generic_base or receiver_type.name):
        return

    shown = display_type(receiver_type)
    builder = er.emit_with(validator.reporter, er.ERR.CE2106, node.loc,
                           type=shown, field=node.member)
    if _is_a_method(validator, receiver_type, node.member):
        builder.note(f"'{shown}.{node.member}()' is a method, not a field").help(
            "call it: write the parentheses")
    elif isinstance(receiver_type, EnumType):
        builder.note(f"'{shown}' is an enum: it carries variants, not fields")
        if is_builtin_wrapper_enum(receiver_type):
            builder.help("take the value first: '??', '.realise(default)' or 'match'")
        else:
            builder.help("read a payload with 'match'")
    else:
        close = suggest_member(names, node.member)
        if close is not None:
            builder.help(f"did you mean '{close}'?")
        elif names:
            builder.help(f"'{shown}' declares {', '.join(repr(n) for n in names)}")
    builder.emit()
