"""Expression validation for type validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics import array_runs
from sushi_lang.semantics.typesys import BuiltinType, ArrayType, DynamicArrayType, EnumType, StructType
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ast import ArrayLiteral, IndexAccess, CastExpr, TryExpr, BinaryOp, UnaryOp, Expr, RangeExpr
from sushi_lang.semantics.type_predicates import is_integer_type, is_numeric_type
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
        array_runs.const_int_reader(validator.const_table, validator.ast_constants,
                                    validator.current_unit_name),
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
    # materializes at the TARGET width (Rust `as` semantics), so it is exempt
    # from the bare-literal i32 range check (CE2070). Mark it before recursing.
    from sushi_lang.semantics.ast import IntLit, UnaryOp
    from sushi_lang.semantics.typesys import BuiltinType
    integer_targets = (
        BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    )
    if expr.target_type in integer_targets:
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


def validate_try_expression(validator: 'TypeValidator', expr: 'TryExpr') -> None:
    """Validate ?? operator usage and annotate AST with inferred types."""
    validator.validate_expression(expr.expr)

    inner_type = validator.infer_expression_type(expr.expr)

    unwrapped_type = None
    success_tag = None
    error_type = None
    error_tag = None

    if inner_type is not None:
        if isinstance(inner_type, EnumType):
            ok_variant = inner_type.get_variant("Ok")
            err_variant = inner_type.get_variant("Err")
            is_result_like = (ok_variant and err_variant and
                             len(ok_variant.associated_types) == 1)

            some_variant = inner_type.get_variant("Some")
            none_variant = inner_type.get_variant("None")
            is_maybe_like = (some_variant and none_variant and
                            len(some_variant.associated_types) == 1 and
                            len(none_variant.associated_types) == 0)

            if not is_result_like and not is_maybe_like:
                er.emit(validator.reporter, er.ERR.CE2507, expr.loc, got=display_type(inner_type))
                return

            if is_result_like:
                unwrapped_type = ok_variant.associated_types[0]
                success_tag = inner_type.get_variant_index("Ok")
                if err_variant.associated_types:
                    error_type = err_variant.associated_types[0]
                error_tag = inner_type.get_variant_index("Err")
            else:  # is_maybe_like
                unwrapped_type = some_variant.associated_types[0]
                success_tag = inner_type.get_variant_index("Some")
                # Maybe-like has no error variant with data
                error_type = None
                error_tag = None
        else:
            er.emit(validator.reporter, er.ERR.CE2507, expr.loc, got=display_type(inner_type))
            return

    if validator.current_function is None:
        # An extension/perk body has no error channel; the collect pass already
        # rejected every `??` in it with CE0131 (#398), so emitting the
        # accidental CE2508 here would only duplicate and mislead. Any
        # other None context keeps the CE2508 backstop.
        if not getattr(validator, "in_extension_context", False):
            er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
        return

    # CW2511: Warn about ?? operator in main function
    # While it works, explicit error handling is clearer at the program entry point
    if validator.current_function.name == "main":
        er.emit(validator.reporter, er.ERR.CW2511, expr.loc)
        # Continue validation - this is just a warning

    func_return_type = validator.current_function.ret

    if func_return_type is None:
        # Function has no return type
        er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
        return

    # Normalize the enclosing function's return type to the interned Result<T, E> enum, whichever
    # way it was spelled: an implicit `fn foo() T` / `fn foo() T | E`, an explicit
    # `fn foo() Result<T, E>` (still a GenericTypeRef), or a signature already resolved in place.
    from sushi_lang.semantics.type_resolution import TypeResolver, resolve_unknown_type
    from sushi_lang.semantics.generics.results import (
        ensure_result_type_in_table, is_result_enum, result_ok_err,
    )

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name

    def intern(ok: 'Type', err: 'Type'):
        return ensure_result_type_in_table(validator.enum_table, ok, err, struct_table=structs)

    if isinstance(func_return_type, GenericTypeRef) and func_return_type.base_name == "Result":
        if len(func_return_type.type_args) != 2:
            er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
            return
        func_return_type = intern(func_return_type.type_args[0], func_return_type.type_args[1])
    elif not is_result_enum(func_return_type):
        if validator.current_function.err_type is not None:
            resolver = TypeResolver(structs, enums)
            err_type_resolved = resolver.resolve(validator.current_function.err_type)
        else:
            err_type_resolved = enums.get("StdError")
        if err_type_resolved is None:
            er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
            return
        func_return_type = intern(func_return_type, err_type_resolved)

    if not is_result_enum(func_return_type):
        er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
        return

    # Note: when ?? is used with Maybe<T>, it still propagates as Result.Err()
    outer_ok_type, outer_err_type = result_ok_err(func_return_type)

    inner_err_type = None
    if isinstance(inner_type, EnumType):
        err_variant = inner_type.get_variant("Err")
        if err_variant and err_variant.associated_types:
            inner_err_type = err_variant.associated_types[0]

    # Strict error-type matching, no conversions. This used to compare str(inner) != str(outer)
    # because the two sides could be different instances of the same type -- the workaround that
    # only existed because Result had no single representation. Both sides now resolve to the
    # interned type, so they are compared as types.
    if inner_err_type is not None and outer_err_type is not None:
        inner_resolved = resolve_unknown_type(inner_err_type, structs, enums)
        outer_resolved = resolve_unknown_type(outer_err_type, structs, enums)
        if inner_resolved != outer_resolved:
            er.emit(validator.reporter, er.ERR.CE2511, expr.loc,
                    ok_type=display_type(outer_ok_type),
                    inner_err=display_type(inner_err_type),
                    outer_err=display_type(outer_err_type))
            return

    _annotate_try_expr(expr, inner_type, unwrapped_type, success_tag,
                      error_type, error_tag, func_return_type)


def _annotate_try_expr(
    expr: 'TryExpr',
    inner_type: 'EnumType',
    unwrapped_type: 'Type',
    success_tag: int,
    error_type: 'Optional[Type]',
    error_tag: 'Optional[int]',
    func_return_type: 'Type'
) -> None:
    """Annotate TryExpr AST node with inferred type information."""
    expr.inferred_inner_type = inner_type
    expr.inferred_unwrapped_type = unwrapped_type
    expr.inferred_success_tag = success_tag
    expr.inferred_error_type = error_type
    expr.inferred_error_tag = error_tag
    expr.inferred_func_return_type = func_return_type


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
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.passes.const_eval import ConstantEvaluator, emit_overflow

    evaluator = ConstantEvaluator(Reporter(), validator.const_table,
                                  validator.ast_constants,
                                  validator.current_unit_name)
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
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.passes.const_eval import ConstantEvaluator

    evaluator = ConstantEvaluator(Reporter(), validator.const_table,
                                  validator.ast_constants,
                                  validator.current_unit_name)
    value = evaluator.evaluate(count, BuiltinType.I64, None)
    if value is None or not isinstance(value.value, int) or isinstance(value.value, bool):
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


def validate_boolean_condition(validator: 'TypeValidator', expr: Expr, context: str) -> None:
    """Validate that an expression is boolean or Result<T, E> for control flow."""
    validator.validate_expression(expr)

    expr_type = validator.infer_expression_type(expr)
    if expr_type is not None:
        if expr_type == BuiltinType.BOOL:
            return

        if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
            return

        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(expr_type, GenericTypeRef) and expr_type.base_name == "Result":
            return

        er.emit_with(validator.reporter, er.ERR.CE2005, expr.loc) \
            .help("use '== 0' or '!= 0' for integer conditions").emit()


def check_propagation_in_expression(expr: Expr) -> bool:
    """Check if expression contains ?? operator (TryExpr)."""
    if isinstance(expr, TryExpr):
        return True

    from sushi_lang.semantics.ast import (
        BinaryOp, UnaryOp, Call, MethodCall, DotCall, IndexAccess, MemberAccess,
        ArrayLiteral, EnumConstructor, CastExpr, RangeExpr
    )

    if isinstance(expr, (BinaryOp, RangeExpr)):
        return (check_propagation_in_expression(expr.left) or
                check_propagation_in_expression(expr.right))

    elif isinstance(expr, UnaryOp):
        return check_propagation_in_expression(expr.expr)

    elif isinstance(expr, (Call, MethodCall, DotCall)):
        if hasattr(expr, 'args') and expr.args:
            return any(check_propagation_in_expression(arg) for arg in expr.args)

    elif isinstance(expr, IndexAccess):
        return (check_propagation_in_expression(expr.array) or
                check_propagation_in_expression(expr.index))

    elif isinstance(expr, MemberAccess):
        return check_propagation_in_expression(expr.receiver)

    elif isinstance(expr, ArrayLiteral):
        if expr.elements:
            return any(check_propagation_in_expression(elem.value) for elem in expr.elements)

    elif isinstance(expr, EnumConstructor):
        if expr.args:
            return any(check_propagation_in_expression(arg) for arg in expr.args)

    elif isinstance(expr, CastExpr):
        return check_propagation_in_expression(expr.expr)

    return False
