"""Expression validation for type validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, NamedTuple, Optional, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.semantics import array_runs
from sushi_lang.semantics.typesys import (
    BuiltinType, ArrayType, DynamicArrayType, EnumType, StructType, deref_type)
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ast import ArrayLiteral, IndexAccess, CastExpr, TryExpr, BinaryOp, UnaryOp, Expr, RangeExpr, MemberAccess
from sushi_lang.semantics.conversions import find_conversion
from sushi_lang.semantics.type_predicates import (
    BUILTIN_INTEGER_TYPES, is_error_type, is_instance_of, is_integer_type, is_numeric_type)
from sushi_lang.semantics.generics.results import result_ok_err
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from .arrays import (
    ARRAY_INDEX, RANGE_BOUND, REPEAT_COUNT, STRING_INDEX, reject_non_i32)
from .compatibility import is_valid_cast
from .utils import validate_constant_array_index
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.semantics.conversions import Conversion
    from sushi_lang.semantics.typesys import Type


def validate_array_literal(validator: 'TypeValidator', expr: ArrayLiteral) -> None:
    """Validate array literal - all elements must have same type."""
    if not expr.elements:
        return

    for element in expr.elements:
        if isinstance(element.value, RangeExpr):
            validate_range_expression(validator, element.value)
        else:
            validator.validate_expression(element.value)
        if element.count is not None:
            reject_non_i32(validator, element.count,
                           validator.validate_expression(element.count),
                           position=REPEAT_COUNT)

    # CE2017 for a repeat count that is not a count, CE2019 for a range that yields nothing,
    # and CE2020 for a range carrying a count. A `const` never arrives here with one, because
    # its evaluator reads the same runs first and `validate_constant` returns on the error --
    # so the two speakers cannot both fire for one literal. The result is discarded: this
    # call is here to SPEAK, and every caller that needs the runs reads them itself.
    array_runs.read_runs(
        expr.elements,
        array_runs.const_int_reader(validator.constant_evaluator()),
        validator.reporter)

    # Rows that disagree are measured against the row type their position stamped: a
    # short row is CE2011 at that row, and not a CE2013 against the first row.
    from .compatibility import reject_array_size_mismatch, short_rows
    short = short_rows(validator, expr)
    for row in short:
        reject_array_size_mismatch(validator, row.resolved_type, row, row.loc)
    if short:
        return

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


def is_indexable(ty) -> bool:
    """An array, or a string: `s[i]` reads its byte at offset i in place (#1091)."""
    return isinstance(ty, (ArrayType, DynamicArrayType)) or ty == BuiltinType.STRING


def validate_index_access(validator: 'TypeValidator', expr: IndexAccess) -> None:
    """Validate array indexing - array must be array type, index must be int."""
    validator.validate_expression(expr.array)
    array_type = validator.infer_expression_type(expr.array)

    reject_non_i32(validator, expr.index, validator.validate_expression(expr.index),
                   position=STRING_INDEX if array_type == BuiltinType.STRING else ARRAY_INDEX)

    if array_type is not None and not is_indexable(array_type):
        er.emit(validator.reporter, er.ERR.CE2114, expr.array.loc,
                type=display_type(array_type))

    if isinstance(array_type, ArrayType):
        validate_constant_array_index(validator, expr.index, array_type.size)


def _reject_self_conversion(validator: 'TypeValidator', expr: CastExpr,
                            conversion: 'Conversion') -> None:
    """CE2523: a cast of a conversion's own pair inside that conversion's body.

    `self` is always a value of the source, so the call never ends. A cast of another
    pair in the body is legal.
    """
    if conversion != validator.body_conversion:
        return
    diag = er.emit_with(validator.reporter, er.ERR.CE2523, expr.loc,
                        source=display_type(conversion.source),
                        target=display_type(conversion.target))
    if conversion.name_span is not None:
        diag.note_at("the conversion is declared here", conversion.name_span,
                     conversion.filename)
    diag.help("build the target value directly, for example with one of its "
              "variants").emit()


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

    # Between two error types, `as` calls the declared conversion of the pair (design
    # 3.2), the one lookup of `semantics/conversions.py`. The target is stamped resolved,
    # so the backend and every later reader see the type and not the written name.
    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    resolved_target = resolve_unknown_type(target_type, structs, enums)
    conversion = find_conversion(validator.tables, source_type, resolved_target)
    expr.inferred_conversion = conversion
    if conversion is not None:
        expr.target_type = conversion.target
        _reject_self_conversion(validator, expr, conversion)

    if not is_valid_cast(source_type, resolved_target, conversion):
        diag = er.emit_with(validator.reporter, er.ERR.CE2014, expr.loc,
                            source=display_type(source_type),
                            target=display_type(target_type))
        if is_error_type(source_type) and is_error_type(resolved_target):
            diag.help(conversion_help(source_type, resolved_target, at_try=False))
        from sushi_lang.semantics.generics.opaque import note_opaque
        note_opaque(diag, source_type, resolved_target).emit()


def refuse_range_value(validator: 'TypeValidator', expr: 'RangeExpr') -> None:
    """CE2122: a range in a position that is not a `foreach` iterable or an array element.

    The two legal positions validate their range with `validate_range_expression`
    directly, so a range that reaches the general dispatch is in any other position
    (#1165). The bounds are still checked.
    """
    er.emit_with(validator.reporter, er.ERR.CE2122, expr.loc) \
        .help("walk it with `foreach`, or spell it into an array: `from([a..b])`") \
        .emit()
    validator.refused_ranges.add(id(expr))
    validate_range_expression(validator, expr)


def validate_range_expression(validator: 'TypeValidator', expr: 'RangeExpr') -> None:
    """Validate a range expression: each bound is an i32 (#870).

    A bound that is not a number at all is the range's own refusal (CE2072). A number of
    any other type is refused by the one i32-position rule.
    """
    walked = [(bound, validator.validate_expression(bound)) for bound in (expr.start, expr.end)]
    for bound, bound_type in walked:
        if bound_type is not None and not is_numeric_type(bound_type):
            er.emit(validator.reporter, er.ERR.CE2072, bound.loc,
                   got=display_type(bound_type), expected="integer type")
            continue
        reject_non_i32(validator, bound, bound_type, position=RANGE_BOUND)


class _Arms(NamedTuple):
    """What a `??` reads off the wrapper it is applied to."""
    value_type: Optional['Type']       # the payload the success arm carries
    success_tag: Optional[int]         # that arm's variant index
    error_type: Optional['Type']       # the failure arm's payload


_NO_ARMS = _Arms(None, None, None)


def validate_try_expression(validator: 'TypeValidator', expr: 'TryExpr') -> None:
    """Validate ?? operator usage and annotate AST with inferred types.

    Four questions, in this order, and each one reports its own fault: what channel
    encloses the `??` (CE0131, CE2508), what the operand under it is (CE2507), whether
    the two failure arms name one type (CE2511), and what the back end reads off the
    node. The channel comes first, so a `??` in a bare body gets CE0131 alone
    (docs/design/error-conversion.md section 4).
    """
    validator.validate_expression(expr.expr)

    inner_type = validator.infer_expression_type(expr.expr)
    channel = _enclosing_channel(validator, expr)
    if channel is None:
        return

    arms = _unwrapped_arms(validator, expr, inner_type)
    if arms is None:
        return

    agreed, conversion = _error_arms_agree(validator, expr, arms, channel)
    if not agreed:
        return

    _annotate_try_expr(expr, inner_type, arms, channel, conversion)


def _unwrapped_arms(validator: 'TypeValidator', expr: 'TryExpr',
                    inner_type: Optional['Type']) -> Optional[_Arms]:
    """What the `Result@(T, E)` under a `??` carries, or None once CE2507 is reported.

    `??` takes a `Result@(T, E)` and nothing else (#1168). It reads the TYPE and not its
    variant names: a `Maybe@(T)` holds no error value, and a user enum shaped like a
    `Result` or a `Maybe` is not one. An uninferrable operand carries nothing and reports
    nothing: the fault that made it uninferrable has a diagnostic of its own already.
    """
    if inner_type is None:
        return _NO_ARMS

    if not (isinstance(inner_type, EnumType) and is_instance_of(inner_type, "Result")):
        diag = er.emit_with(validator.reporter, er.ERR.CE2507, expr.loc,
                            got=display_type(inner_type))
        if is_instance_of(inner_type, "Maybe"):
            diag.help("a `Maybe` holds no error value, so write one with `or_err`: "
                      "`m.or_err(nom <error value>)??`")
        else:
            diag.help("answer a `Result@(T, E)` here (the callee writes `| E`), "
                      "or use the value without `??`")
        diag.emit()
        return None

    ok_type, err_type = result_ok_err(inner_type)
    return _Arms(value_type=ok_type,
                 success_tag=inner_type.get_variant_index("Ok"),
                 error_type=err_type)


def _enclosing_channel(validator: 'TypeValidator', expr: 'TryExpr') -> Optional['Type']:
    """The interned `Result@(T, E)` a `??` propagates into, or None once it is reported.

    A BARE body has no channel (docs/design/error-channel.md). The collect pass already
    refused every `??` in a written function or method body with CE0131, so a second
    diagnostic here would only mislead. A lambda is the one body that pass cannot judge:
    its channel comes from its TYPE, which this pass infers, so its CE0131 is emitted
    here. A `??` outside every body keeps the CE2508 backstop.
    """
    channel = validator.channel_result
    if channel is not None:
        return channel
    if validator.body_name is None:
        er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
    elif validator.body_name == "lambda":
        er.emit_with(validator.reporter, er.ERR.CE0131, expr.loc,
                     context="a lambda without '| E'") \
            .help("write '| E' in the function type the lambda takes, or handle the "
                  "value in the body (match, .realise(default))").emit()
    return None


def _error_arms_agree(validator: 'TypeValidator', expr: 'TryExpr', arms: _Arms,
                      channel: 'Type') -> Tuple[bool, Optional['Conversion']]:
    """Whether the operand's failure arm reaches the channel's, and through which conversion.

    The same type propagates unchanged. Two error types propagate through the declared
    conversion of the pair, the one lookup of `semantics/conversions.py`, and nothing
    else (docs/design/error-conversion.md section 3.3): a chain is never followed.
    Otherwise it is CE2511, and the help names the declaration to write. In a generic
    function the pair is asked one time, on the template (#1070): an opaque `E` is equal
    to itself alone, so a `??` propagates the same `E` and nothing else (R8).
    """
    outer_ok_type, outer_err_type = result_ok_err(channel)
    inner_err_type = arms.error_type

    if inner_err_type is None:
        return True, None

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    inner = resolve_unknown_type(inner_err_type, structs, enums)
    outer = resolve_unknown_type(outer_err_type, structs, enums)
    if inner == outer:
        return True, None

    conversion = find_conversion(validator.tables, inner, outer)
    if conversion is not None:
        return True, conversion

    from sushi_lang.semantics.generics.opaque import note_opaque
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE2511, expr.loc,
                              ok_type=display_type(outer_ok_type),
                              inner_err=display_type(inner_err_type),
                              outer_err=display_type(outer_err_type))
    note_opaque(diagnostic, inner, outer) \
        .help(conversion_help(inner, outer, at_try=True)).emit()
    return False, None


def conversion_help(source: 'Type', target: 'Type', *, at_try: bool) -> str:
    """The help that names the conversion to declare, for `??` (CE2511) and `as` (CE2014).

    It names only a declaration that the collect pass accepts. When no declaration of
    the pair is legal (a generic side, C6; a target with no home module, C12), it names
    the form at the site: `.map_err(f)??` for a `??`, and a function that takes the error
    `nom` for an `as`. It never offers `r?? as T`: `as` binds after `??`, so that
    spelling casts the VALUE.
    """
    from sushi_lang.semantics.passes.collect.enums import PREDEFINED_ENUM_HOMES
    from sushi_lang.semantics.generics.opaque import holds_opaque
    source_text, target_text = display_type(source), display_type(target)
    if holds_opaque(source) or holds_opaque(target):
        # R8 (#1070): no conversion can name a type parameter, so a `??` propagates the
        # same error type and no other.
        return (f"a type parameter has no conversion, so only the same error type "
                f"propagates; {_site_form(source_text, target_text, at_try)}, or write "
                f"the concrete error type in the signature")
    generic = next((side for side in (source, target) if getattr(side, "generic_args", None)),
                   None)
    if generic is not None:
        return (f"a conversion cannot take the generic error type "
                f"'{display_type(generic)}'; {_site_form(source_text, target_text, at_try)}")
    declaration = f"`extend {source_text} as {target_text}:`"
    name = getattr(target, "name", None)
    if name in PREDEFINED_ENUM_HOMES:
        home = PREDEFINED_ENUM_HOMES[name]
        if home is None:
            return (f"no unit may declare a conversion into '{target_text}', because it "
                    f"has no home module; {_site_form(source_text, target_text, at_try)}, "
                    f"or use an error type of your own as the target, and declare "
                    f"`extend {source_text} as <YourError>:` in the unit that declares it")
        return (f"only <{home}> may declare {declaration}; answer an error type of "
                f"your own, and convert both errors into it")
    return (f"declare {declaration} in the unit that declares '{target_text}', and "
            f"return the converted value")


def _site_form(source_text: str, target_text: str, at_try: bool) -> str:
    """The conversion at one site, with no declaration: `map_err` for `??`, a call for `as`."""
    if at_try:
        return (f"convert the error at this site with `.map_err(f)??`, where `f` takes "
                f"'{source_text}' `nom` and answers '{target_text}'")
    return f"call a function that takes the error `nom` and answers '{target_text}'"


def _annotate_try_expr(expr: 'TryExpr', inner_type: Optional['Type'],
                       arms: _Arms, channel: 'Type',
                       conversion: Optional['Conversion']) -> None:
    """Annotate TryExpr AST node with inferred type information."""
    expr.inferred_inner_type = inner_type
    expr.inferred_unwrapped_type = arms.value_type
    expr.inferred_success_tag = arms.success_tag
    expr.inferred_error_type = arms.error_type
    expr.inferred_func_return_type = channel
    expr.inferred_conversion = conversion


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
        wrapper = _wrapper_of(operand_type)
        if wrapper is not None:
            report = report.help(f"take the value with {take_the_value(wrapper[0])}")
        from sushi_lang.semantics.generics.opaque import explain_no_arithmetic
        explain_no_arithmetic(report, operand_type).emit()
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


def top_level_contract(validator: 'TypeValidator', ty: 'Optional[Type]',
                        contract: str, refused: 'Optional[list]' = None
                        ) -> 'tuple[bool, Optional[str]]':
    """May a value of `ty` meet the positions of `contract` at the top level?

    `Eq` and `Ord` are the operators, the array search methods (`contains`, `index_of`)
    and the method call (CE2100 cites CE2514 for a reason); `Display` is an
    interpolation hole and `print`/`println`. THE rule, in one place, so the positions
    cannot drift apart: a primitive keeps its closed set, and a struct or an enum asks
    the derived contract, with the compilation's override. A printed position also
    takes an array, a `List@(T)` and an `Own@(T)` (`printed_contract`). `refused`,
    when given, receives the types that the rule refuses, for the help of a refusal.
    """
    from sushi_lang.semantics.generics.contracts import (
        DISPLAY, operand_contract, override_of, printed_contract)
    overridden = override_of(validator.derived_methods, contract)
    if contract == DISPLAY:
        return printed_contract(ty, overridden=overridden, refused=refused)
    return operand_contract(ty, contract, overridden=overridden, refused=refused)


def has_equality(validator: 'TypeValidator', ty: 'Type',
                 refused: 'Optional[list]' = None) -> bool:
    """Can two values of `ty` meet `==`?"""
    from sushi_lang.semantics.generics.contracts import EQ
    return top_level_contract(validator, ty, EQ, refused)[0]


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
        from sushi_lang.semantics.generics.opaque import note_opaque
        note_opaque(er.emit_with(validator.reporter, er.ERR.CE2513, expr.loc,
                                 left_type=display_type(left_type),
                                 right_type=display_type(right_type), op=expr.op),
                    left_type, right_type).emit()
        return

    from sushi_lang.semantics.generics.contracts import EQ, ORD
    contract = EQ if expr.op in _EQUALITY_OPS else ORD
    refused: list = []
    permitted, reason = top_level_contract(validator, left_type, contract, refused)
    if permitted:
        operand = deref_type(left_type)
        if not isinstance(operand, BuiltinType):
            expr.operand_type = operand
        return

    builder = er.emit_with(validator.reporter, er.ERR.CE2514, expr.loc,
                           op=expr.op, type_name=display_type(left_type))
    if reason is not None:
        builder = builder.note(f"no derived {contract}: {reason}")
    escape = _comparison_escape(left_type)
    if escape is not None:
        builder = builder.help(escape)
    from sushi_lang.semantics.generics.opaque import explain_unpromised
    explain_unpromised(builder, contract, refused).emit()


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


def take_the_value(wrapper: str) -> str:
    """The ways to take the value out of a wrapper, in the words of a help.

    `??` takes a Result only (docs/design/error-conversion.md section 4), so a Maybe is
    given an error value first.
    """
    if wrapper == "Maybe":
        return "'.realise(default)', match, or '.or_err(nom e)??'"
    return "'??', '.realise(default)' or match"


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

    The ONE seam for every condition position: an `if`, a `while` (#522), an `assert`,
    and the operands of `and`, `or`, `xor` and `not` (#532). A wrapper is told which predicate
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
                  f"{take_the_value(name)}").emit()
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
    # An opaque type parameter has the methods its constraints promise, and no field.
    from sushi_lang.semantics.generics.types import TypeParameter
    if isinstance(receiver_type, TypeParameter) and receiver_type.is_opaque:
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
    from sushi_lang.semantics.passes.types.visibility import type_is_contested
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
    # A type name this unit declared and LOST holds the winner's fields or variants, not
    # the unit's own; the loser already heard CE0004, CE0006 or CE3011 at its
    # declaration (#738, #921).
    if type_is_contested(validator, receiver_type):
        return

    shown = display_type(receiver_type)
    builder = er.emit_with(validator.reporter, er.ERR.CE2106, node.member_span or node.loc,
                           type=shown, field=node.member)
    if _is_a_method(validator, receiver_type, node.member):
        builder.note(f"'{shown}.{node.member}()' is a method, not a field").help(
            "call it: write the parentheses")
    elif isinstance(receiver_type, EnumType):
        builder.note(f"'{shown}' is an enum: it carries variants, not fields")
        if is_builtin_wrapper_enum(receiver_type):
            builder.help(
                f"take the value first with {take_the_value(receiver_type.generic_base)}")
        else:
            builder.help("read a payload with 'match'")
    else:
        close = suggest_member(names, node.member)
        if close is not None:
            builder.help(f"did you mean '{close}'?")
        elif names:
            builder.help(f"'{shown}' declares {', '.join(repr(n) for n in names)}")
    # A type parameter has no field: a constraint promises methods alone (#1070).
    from sushi_lang.semantics.generics.opaque import note_opaque
    note_opaque(builder, receiver_type).emit()
