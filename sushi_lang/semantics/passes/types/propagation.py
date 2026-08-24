"""Type propagation utilities for generic types."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.typesys import (EnumType, StructType, BuiltinType, ArrayType,
                                          DynamicArrayType)
from sushi_lang.semantics.ast import (EnumConstructor, DotCall, Call, Name, IntLit, FloatLit,
                                      UnaryOp, BinaryOp, ArrayLiteral, DynamicArrayFrom)
from sushi_lang.internals import errors as er
from .inference import int_literal_fits, float_literal_fits

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.semantics.ast import Expr
    from sushi_lang.semantics.typesys import Type

_NUMERIC_INT = {BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64}
_NUMERIC_FLOAT = {BuiltinType.F32, BuiltinType.F64}

_ARITH_BITWISE_OPS = {"+", "-", "*", "/", "%", "&", "|", "^"}
_SHIFT_OPS = {"<<", ">>"}
# A unary operator whose result is its operand's type. `not` answers a bool and is not one.
_TYPE_PRESERVING_UNARY = {"neg", "~"}


def _is_negated_literal(expr: 'Expr') -> bool:
    """A negated bare literal is ONE leaf: the sign belongs to its range check.

    That is what makes -128 fit an i8 while the bare 128 does not, and -1 miss a u8.
    """
    return (isinstance(expr, UnaryOp) and expr.op == "neg"
            and isinstance(expr.expr, (IntLit, FloatLit)))


def is_bare_numeric_literal(expr: 'Expr') -> bool:
    """A numeric literal that no context has typed yet."""
    return isinstance(expr, (IntLit, FloatLit)) and expr.resolved_type is None


def unwrap_type_preserving_unary(expr: 'Expr') -> 'Expr':
    """The value under any run of unary operators that keep their operand's type."""
    while isinstance(expr, UnaryOp) and expr.op in _TYPE_PRESERVING_UNARY:
        expr = expr.expr
    return expr


def _propagate_numeric_type(validator: 'TypeValidator', expr: 'Expr',
                            expected: BuiltinType) -> None:
    """Push an expected numeric type into a value's literal leaves."""
    if isinstance(expr, BinaryOp):
        if expr.op in _ARITH_BITWISE_OPS:
            _propagate_numeric_type(validator, expr.left, expected)
            _propagate_numeric_type(validator, expr.right, expected)
        elif expr.op in _SHIFT_OPS:
            _propagate_numeric_type(validator, expr.left, expected)
        return
    if (isinstance(expr, UnaryOp) and expr.op in _TYPE_PRESERVING_UNARY
            and not _is_negated_literal(expr)):
        _propagate_numeric_type(validator, expr.expr, expected)
        return
    _stamp_numeric_literal(validator, expr, expected)


def _stamp_numeric_literal(validator: 'TypeValidator', node: 'Expr',
                           expected: BuiltinType) -> None:
    """Stamp a bare numeric literal (optionally negated) with its context type."""
    sign = 1
    lit = node
    if _is_negated_literal(lit):
        sign = -1
        lit = lit.expr

    # Idempotent: a literal may be reached by more than one propagation shim
    # (enum + struct dotcall helpers both delegate here). Stamp once so we neither
    # emit a duplicate CE2073 nor re-walk.
    if isinstance(lit, (IntLit, FloatLit)) and lit.resolved_type is not None:
        return

    if isinstance(lit, IntLit) and expected in _NUMERIC_INT:
        value = sign * int(lit.value)
        radix = 10 if sign == -1 else lit.radix
        if not int_literal_fits(value, radix, expected):
            er.emit(validator.reporter, er.ERR.CE2073, lit.loc,
                    literal=str(value), type=expected.value)
        # Stamp even on failure: CE2073 is authoritative and compilation aborts, but
        # stamping keeps downstream type inference consistent (no secondary CE2049).
        lit.resolved_type = expected
        lit.range_checked = True
    elif isinstance(lit, FloatLit) and expected in _NUMERIC_FLOAT:
        value = sign * float(lit.value)
        if not float_literal_fits(value, expected):
            er.emit(validator.reporter, er.ERR.CE2073, lit.loc,
                    literal=str(value), type=expected.value)
        lit.resolved_type = expected


def _propagate_array_element_type(validator: 'TypeValidator', value_expr: 'Expr',
                                  element_type: 'Type') -> None:
    """Push an expected element type into an array literal's elements."""
    if isinstance(value_expr, DynamicArrayFrom):
        value_expr = value_expr.elements
    if not isinstance(value_expr, ArrayLiteral):
        return
    for element in value_expr.elements:
        propagate_types_to_value(validator, element, element_type)


def _propagate_to_enum_args(validator: 'TypeValidator', node: Expr,
                            enum_type: EnumType) -> None:
    """Recursively propagate types to enum constructor arguments."""
    if not node.args:
        return

    variant_name = None
    if isinstance(node, EnumConstructor):
        variant_name = node.variant_name
    elif isinstance(node, DotCall):
        variant_name = node.method

    if not variant_name:
        return

    variant = None
    for v in enum_type.variants:
        if v.name == variant_name:
            variant = v
            break

    if not variant or not variant.associated_types:
        return

    # The variant's associated types ARE the expected argument types.
    for i, arg in enumerate(node.args):
        if i < len(variant.associated_types):
            propagate_types_to_value(validator, arg, variant.associated_types[i])


def _propagate_to_struct_args(validator: 'TypeValidator', node: Expr,
                              struct_type: StructType) -> None:
    """Recursively propagate types to struct constructor arguments."""
    if not node.args:
        return

    field_names = getattr(node, "field_names", None)
    if field_names:
        by_name = dict(struct_type.fields)
        # strict=False: a name/argument count mismatch is a user arity error that
        # constructor validation reports properly. Propagation must not raise on it.
        for name, arg in zip(field_names, node.args, strict=False):
            if name in by_name:
                propagate_types_to_value(validator, arg, by_name[name])
        return

    # Match constructor arguments to struct fields
    # Both Call and DotCall nodes have args attribute
    # struct_type.fields is a tuple of (field_name, field_type) tuples
    for i, arg in enumerate(node.args):
        if i < len(struct_type.fields):
            field_name, field_type = struct_type.fields[i]
            propagate_types_to_value(validator, arg, field_type)


def _propagate_generic_enum_type(validator: 'TypeValidator', node: Expr,
                                 enum_type: EnumType) -> None:
    """Propagate generic enum type (Maybe, Either, user-defined) to constructor."""
    if not isinstance(node, (EnumConstructor, DotCall)):
        return

    enum_name = None
    if isinstance(node, EnumConstructor):
        enum_name = node.enum_name
    elif isinstance(node, DotCall) and isinstance(node.receiver, Name):
        enum_name = node.receiver.id

    if not (enum_name and isinstance(enum_type, EnumType)):
        return

    if enum_name in validator.generic_enum_table.by_name:
        node.resolved_enum_type = enum_type

        _propagate_to_enum_args(validator, node, enum_type)

    # A PLAIN enum still propagates to its arguments: only the `resolved_enum_type` stamp
    # above is generic-specific, while the variant payload types are the expected argument
    # types either way. Without this, `Boxed.Wrap(Own.alloc(l))` died as CE0055 (#265).
    #
    # Keyed on an EXACT name match, so a constructor for another enum cannot be handed
    # this enum's payload types.
    elif enum_name == enum_type.name:
        _propagate_to_enum_args(validator, node, enum_type)


def _propagate_generic_struct_type(validator: 'TypeValidator', node: Expr,
                                   struct_type: StructType) -> None:
    """Propagate generic struct type (Own, Box, Pair, user-defined) to constructor."""
    if isinstance(node, DotCall) and isinstance(node.receiver, Name):
        struct_name = node.receiver.id

        if (struct_name in validator.generic_struct_table.by_name and
            isinstance(struct_type, StructType)):
            node.resolved_struct_type = struct_type

            # Own<T>'s only field is a PointerType, which the generic field walk below has
            # no branch for, so it would drop the type and stamp nothing (#135). Propagate
            # the UNWRAPPED element type instead.
            if struct_name == "Own" and node.args:
                from sushi_lang.semantics.generics.own import get_own_element_type
                propagate_types_to_value(
                    validator, node.args[0], get_own_element_type(struct_type))
                return

            _propagate_to_struct_args(validator, node, struct_type)

    elif isinstance(node, Call) and hasattr(node.callee, 'id'):
        struct_name = node.callee.id

        if not isinstance(struct_type, StructType):
            return

        if struct_name in validator.generic_struct_table.by_name:
            # Update the Call node's callee id to use the concrete type name
            # This allows validate_struct_constructor to find the right struct
            # e.g., Box -> Box<i32>
            node.callee.id = struct_type.name

            _propagate_to_struct_args(validator, node, struct_type)

        # A CONCRETE struct constructor hands its field types down too. Covering only
        # generic ones stopped propagation at `Own.alloc(Holder(0, Maybe.None()))`.
        elif struct_name == struct_type.name:
            _propagate_to_struct_args(validator, node, struct_type)


def propagate_declared_type_to_value(validator: 'TypeValidator', value_expr: Expr,
                                     declared_type: Optional['Type']) -> Optional['Type']:
    """Propagate a DECLARED type into a value, and answer what it resolves to.

    `propagate_types_to_value` acts on a RESOLVED type: a `Maybe@(i32)` still spelled as a
    GenericTypeRef matches no arm and stamps nothing. A declared position -- an extension
    or perk method's return type, or its parameter -- holds the spelling, so it resolves
    first (#387).
    """
    if declared_type is None:
        return None

    from .utils import resolve_declared_type
    resolved = resolve_declared_type(validator, declared_type)
    propagate_types_to_value(validator, value_expr, resolved)
    return resolved


def propagate_types_to_value(validator: 'TypeValidator', value_expr: Expr,
                            expected_type: 'Type') -> None:
    """Unified entry point for all type propagation."""
    if isinstance(expected_type, BuiltinType) and (
            expected_type in _NUMERIC_INT or expected_type in _NUMERIC_FLOAT):
        _propagate_numeric_type(validator, value_expr, expected_type)
        return

    # An array literal's elements take the DECLARED element type, in every position that
    # has one. Without this arm the elements saw no context at all: a wide literal was
    # range-checked against the default i32 (CE2070, the code for no context), and a plain
    # `[1, 2]` inferred `i32[2]` and was rejected against an `i64[2]` field, argument,
    # return or enum payload (#378).
    if isinstance(expected_type, (ArrayType, DynamicArrayType)):
        _propagate_array_element_type(validator, value_expr, expected_type.base_type)
        return

    # Hand a lambda its expected FunctionType so bare-name params (`|x|`) infer, and a
    # bare Name its expected fn type so a generic-fn reference can solve its type args.
    from sushi_lang.semantics.typesys import FunctionType as _FunctionType
    from sushi_lang.semantics.ast import Lambda as _Lambda, Name as _Name
    if isinstance(expected_type, _FunctionType) and isinstance(value_expr, (_Lambda, _Name)):
        value_expr.expected_type = expected_type
        return

    # Generic enum propagation -- Result, Maybe, Either, user-defined. Result used to have a
    # parallel path of its own here, because it arrived as a ResultType rather than an EnumType.
    if isinstance(expected_type, EnumType):
        _propagate_generic_enum_type(validator, value_expr, expected_type)

    elif isinstance(expected_type, StructType):
        _propagate_generic_struct_type(validator, value_expr, expected_type)
