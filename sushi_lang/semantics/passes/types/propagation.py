# semantics/passes/types/propagation.py
"""
Type propagation utilities for generic types.

This module provides utilities for propagating expected types to enum and struct
constructors, enabling type inference for generic types (Result<T, E>, Maybe<T>,
Own<T>, user-defined generics, etc.).

Type propagation MUST happen BEFORE validation to enable proper type resolution.

PUBLIC API:
    propagate_types_to_value() - Unified entry point for all type propagation

All other functions are private (prefixed with _) and should not be called
directly. The public entry point handles all propagation cases:
- Result<T, E> propagation to Result.Ok/Err
- Generic enum propagation (Maybe<T>, Either<T, U>, user-defined)
- Generic struct propagation (Own<T>, Box<T>, Pair<T, U>, user-defined)
- Nested generic propagation (Result.Ok(Maybe.Some(x)))

Extracted from validate_return_statement() and validate_let_statement() to
eliminate duplication across statement validators.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.semantics.typesys import EnumType, StructType, BuiltinType
from sushi_lang.semantics.ast import EnumConstructor, DotCall, Call, Name, IntLit, FloatLit, UnaryOp, BinaryOp
from sushi_lang.internals import errors as er
from .inference import int_literal_fits, float_literal_fits

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.semantics.ast import Expr
    from sushi_lang.semantics.typesys import Type

_NUMERIC_INT = {BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64}
_NUMERIC_FLOAT = {BuiltinType.F32, BuiltinType.F64}

# Binary operators whose result shares the operand type, so an expected numeric type
# flows into both operands (`let u32 m = 0x01 | 0x02`).
_ARITH_BITWISE_OPS = {"+", "-", "*", "/", "%", "&", "|", "^"}
# Shift operators: the expected type flows into the shifted value (left) only; the
# shift amount (right) is typed independently.
_SHIFT_OPS = {"<<", ">>"}


def _propagate_numeric_type(validator: 'TypeValidator', expr: 'Expr',
                            expected: BuiltinType) -> None:
    """Push an expected numeric type into a value's literal leaves.

    Recurses through arithmetic/bitwise binary ops (both operands) and shifts
    (left operand only) so `const u32 FLAGS = 0x01 | 0x02 | 0x04` stamps every leaf
    u32. Comparison/logical ops are not recursed (their result is bool). Non-binary
    leaves are handed to `_stamp_numeric_literal`.
    """
    if isinstance(expr, BinaryOp):
        if expr.op in _ARITH_BITWISE_OPS:
            _propagate_numeric_type(validator, expr.left, expected)
            _propagate_numeric_type(validator, expr.right, expected)
        elif expr.op in _SHIFT_OPS:
            _propagate_numeric_type(validator, expr.left, expected)
        return
    _stamp_numeric_literal(validator, expr, expected)


def _stamp_numeric_literal(validator: 'TypeValidator', node: 'Expr',
                           expected: BuiltinType) -> None:
    """Stamp a bare numeric literal (optionally negated) with its context type.

    Range-checks the literal against the expected type; out-of-range emits CE2073.
    Sets `resolved_type` (read by inference and the backend) and `range_checked`
    (so the default-i32 CE2070 check in the validator skips it). A non-literal leaf
    (a Name, a call result) is left untouched so a typed value still requires `as`.
    """
    sign = 1
    lit = node
    if (isinstance(lit, UnaryOp) and lit.op == "neg"
            and isinstance(lit.expr, (IntLit, FloatLit))):
        sign = -1
        lit = lit.expr

    # Idempotent: a literal may be reached by more than one propagation shim
    # (enum + struct dotcall helpers both delegate here). Stamp once so we neither
    # emit a duplicate CE2073 nor re-walk.
    if isinstance(lit, (IntLit, FloatLit)) and lit.resolved_type is not None:
        return

    if isinstance(lit, IntLit) and expected in _NUMERIC_INT:
        value = sign * int(lit.value)
        # A negated literal is a signed value, so use value (decimal) semantics.
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


def _propagate_to_enum_args(validator: 'TypeValidator', node: Expr,
                            enum_type: EnumType) -> None:
    """Recursively propagate types to enum constructor arguments.

    Args:
        validator: The type validator instance
        node: The enum constructor node (EnumConstructor or DotCall)
        enum_type: The concrete EnumType with variant field types
    """
    if not node.args:
        return

    # Extract variant name
    variant_name = None
    if isinstance(node, EnumConstructor):
        variant_name = node.variant_name
    elif isinstance(node, DotCall):
        variant_name = node.method

    if not variant_name:
        return

    # Find the variant in the enum type
    variant = None
    for v in enum_type.variants:
        if v.name == variant_name:
            variant = v
            break

    if not variant or not variant.associated_types:
        return

    # Propagate the variant's associated types to the arguments
    # For Result.Ok(x), associated_types[0] is T
    # For Result.Err(x), associated_types[0] is E
    # For Maybe.Some(x), associated_types[0] is T
    for i, arg in enumerate(node.args):
        if i < len(variant.associated_types):
            propagate_types_to_value(validator, arg, variant.associated_types[i])


def _propagate_to_struct_args(validator: 'TypeValidator', node: Expr,
                              struct_type: StructType) -> None:
    """Recursively propagate types to struct constructor arguments.

    Args:
        validator: The type validator instance
        node: The struct constructor node (Call or DotCall)
        struct_type: The concrete StructType with field types
    """
    if not node.args:
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
    """Propagate generic enum type (Maybe, Either, user-defined) to constructor.

    Sets resolved_enum_type on EnumConstructor or DotCall nodes.

    Args:
        validator: The type validator instance
        node: The enum constructor node (Maybe.Some, Either.Left, etc.)
        enum_type: The expected concrete EnumType

    Consolidates lines 122-126 (let), 369-382 (rebind) from statements.py.
    """
    if not isinstance(node, (EnumConstructor, DotCall)):
        return

    # Extract enum name from constructor
    enum_name = None
    if isinstance(node, EnumConstructor):
        enum_name = node.enum_name
    elif isinstance(node, DotCall) and isinstance(node.receiver, Name):
        enum_name = node.receiver.id

    # Check if this is a generic enum constructor
    if (enum_name and enum_name in validator.generic_enum_table.by_name and
        isinstance(enum_type, EnumType)):
        # Store the resolved enum type in the AST node for backend
        node.resolved_enum_type = enum_type

        # Recursively propagate to constructor arguments
        _propagate_to_enum_args(validator, node, enum_type)


def _propagate_generic_struct_type(validator: 'TypeValidator', node: Expr,
                                   struct_type: StructType) -> None:
    """Propagate generic struct type (Own, Box, Pair, user-defined) to constructor.

    Handles both DotCall constructors (Own.alloc) and Call constructors (Box).

    Args:
        validator: The type validator instance
        node: The struct constructor node
        struct_type: The expected concrete StructType

    Consolidates lines 128-151 (let), 291-308 (return) from statements.py.
    """
    # Handle DotCall constructors (e.g., Own.alloc(42))
    if isinstance(node, DotCall) and isinstance(node.receiver, Name):
        struct_name = node.receiver.id

        # Check if this is a generic struct constructor
        if (struct_name in validator.generic_struct_table.by_name and
            isinstance(struct_type, StructType)):
            # Store the resolved struct type in the AST node for backend
            node.resolved_struct_type = struct_type

            # Own<T>'s only field is T* (a PointerType), which propagate_types_to_value has
            # no branch for -- so the generic field walk below would pass the pointer-wrapped
            # element type, drop it, and never stamp resolved_enum_type / resolved_struct_type
            # on an inline `Own.alloc(<constructor>)` argument (#135: CE0113 for a generic-enum
            # element, CE0000 for a generic-struct element). Propagate the UNWRAPPED element
            # type instead, mirroring the normal-call argument path.
            if struct_name == "Own" and node.args:
                from sushi_lang.semantics.generics.own import get_own_element_type
                propagate_types_to_value(
                    validator, node.args[0], get_own_element_type(struct_type))
                return

            # Recursively propagate to constructor arguments
            _propagate_to_struct_args(validator, node, struct_type)

    # Handle Call constructors (e.g., Box(42))
    elif isinstance(node, Call) and hasattr(node.callee, 'id'):
        struct_name = node.callee.id

        # Check if this is a generic struct constructor
        if (struct_name in validator.generic_struct_table.by_name and
            isinstance(struct_type, StructType)):
            # Update the Call node's callee id to use the concrete type name
            # This allows validate_struct_constructor to find the right struct
            # e.g., Box -> Box<i32>
            node.callee.id = struct_type.name

            # Recursively propagate to constructor arguments
            _propagate_to_struct_args(validator, node, struct_type)


def propagate_types_to_value(validator: 'TypeValidator', value_expr: Expr,
                            expected_type: 'Type') -> None:
    """Unified entry point for all type propagation.

    Propagates expected types to constructors in the value expression,
    enabling type inference for generic types. This MUST be called BEFORE
    expression validation.

    Handles:
    - Result<T, E> propagation to Result.Ok/Err
    - Generic enum propagation (Maybe<T>, Either<T, U>, user-defined)
    - Generic struct propagation (Own<T>, Box<T>, Pair<T, U>, user-defined)
    - Nested generic propagation (Result.Ok(Maybe.Some(x)))

    Args:
        validator: The type validator instance
        value_expr: The expression to propagate types to
        expected_type: The expected type from context (variable type, return type, etc.)

    This orchestrator replaces duplicated propagation logic across
    validate_return_statement(), validate_let_statement(), and
    validate_rebind_statement().
    """
    # Context-typed numeric literals: stamp bare literal leaves with the expected
    # type, recursing through arithmetic/bitwise/shift operands.
    if isinstance(expected_type, BuiltinType) and (
            expected_type in _NUMERIC_INT or expected_type in _NUMERIC_FLOAT):
        _propagate_numeric_type(validator, value_expr, expected_type)
        return

    # Function-typed context: hand a lambda its expected FunctionType so bare-name
    # params (`|x|`) infer their types from the binding/argument context; likewise hand
    # a bare Name its expected fn type so a generic-fn reference (`let fn(i32)->i32 g =
    # identity`) can solve its type args (T2.3).
    from sushi_lang.semantics.typesys import FunctionType as _FunctionType
    from sushi_lang.semantics.ast import Lambda as _Lambda, Name as _Name
    if isinstance(expected_type, _FunctionType) and isinstance(value_expr, (_Lambda, _Name)):
        value_expr.expected_type = expected_type
        return

    # Generic enum propagation -- Result, Maybe, Either, user-defined. Result used to have a
    # parallel path of its own here, because it arrived as a ResultType rather than an EnumType.
    if isinstance(expected_type, EnumType):
        _propagate_generic_enum_type(validator, value_expr, expected_type)

    # Handle generic struct propagation (Own, Box, Pair, user-defined)
    elif isinstance(expected_type, StructType):
        _propagate_generic_struct_type(validator, value_expr, expected_type)
