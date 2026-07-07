# semantics/passes/types/inference.py
"""
Type inference helpers for type validation.

This module contains helper functions for inferring types from expressions:
- Array literal type inference
- Index access type inference
- Dynamic array from() constructor type inference
- Contextual type inference for literals
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import Type, BuiltinType, ArrayType, DynamicArrayType, IteratorType
from sushi_lang.semantics.ast import IntLit, FloatLit, ArrayLiteral, IndexAccess, DynamicArrayFrom, Expr, RangeExpr

if TYPE_CHECKING:
    from . import TypeValidator


def infer_array_literal_type(validator: 'TypeValidator', expr: ArrayLiteral) -> Optional[Type]:
    """Infer type of array literal based on elements (validates all elements match)."""
    if not expr.elements:
        # Empty array - can't infer type without context
        return None

    # Infer type from first element
    first_element_type = validator.infer_expression_type(expr.elements[0])
    if first_element_type is None:
        return None

    # Verify all elements have the same type (CE2013)
    for i, element in enumerate(expr.elements[1:], start=1):
        element_type = validator.infer_expression_type(element)
        if element_type is not None and element_type != first_element_type:
            # Type mismatch in array literal elements
            er.emit(validator.reporter, er.ERR.CE2013, element.loc,
                   expected=str(first_element_type), got=str(element_type))
            # Continue checking other elements

    return ArrayType(base_type=first_element_type, size=len(expr.elements))


def infer_index_access_type(validator: 'TypeValidator', expr: IndexAccess) -> Optional[Type]:
    """Infer type of array indexing - should return element type."""
    array_type = validator.infer_expression_type(expr.array)
    if array_type is None:
        return None

    # Both fixed (T[N]) and dynamic (T[]) arrays index to their element type
    if isinstance(array_type, (ArrayType, DynamicArrayType)):
        return array_type.base_type

    # If not an array type, this will be caught by other validation
    return None


def infer_dynamic_array_from_type(validator: 'TypeValidator', expr: DynamicArrayFrom, expected_type: Optional[DynamicArrayType] = None) -> Optional[Type]:
    """Infer type of from(array_literal) constructor from array literal elements.

    Args:
        validator: The TypeValidator instance.
        expr: The DynamicArrayFrom expression to infer
        expected_type: Optional expected type for contextual type inference (e.g., u8[] provides u8 context)

    Returns:
        Inferred DynamicArrayType or None if type cannot be inferred
    """
    array_literal = expr.elements
    if not array_literal.elements:
        # Empty array - can't infer type
        return None

    # Get the expected element type for contextual inference
    expected_element_type = expected_type.base_type if expected_type else None

    # Infer type from first element with contextual typing
    first_element_type = infer_element_type_with_context(validator, array_literal.elements[0], expected_element_type)
    if first_element_type is None:
        return None

    # Validate all elements have the same type (with contextual typing)
    for element in array_literal.elements[1:]:
        element_type = infer_element_type_with_context(validator, element, expected_element_type)
        if element_type != first_element_type:
            # Type mismatch - will be caught by validation elsewhere
            return None

    return DynamicArrayType(base_type=first_element_type)


def infer_element_type_with_context(validator: 'TypeValidator', expr: Expr, expected_type: Optional[Type]) -> Optional[Type]:
    """Infer type of an array element expression with optional contextual type hint.

    This method enables contextual type inference for array literals within from() constructors.
    When the LHS declares a type like u8[], integer literals in the array will infer to u8 instead of i32.

    Args:
        validator: The TypeValidator instance.
        expr: The expression to infer the type of
        expected_type: Optional expected type from the LHS declaration (e.g., u8 from u8[])

    Returns:
        Inferred type, using expected_type for integer/float literals when provided
    """
    # Context-type a bare numeric literal to the expected element type (stamps the
    # literal, range-checks it, emits CE2073 on overflow). Shares the single
    # propagation path so dynamic-array elements behave like every other context.
    if expected_type is not None and isinstance(expected_type, BuiltinType):
        from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
        propagate_types_to_value(validator, expr, expected_type)

    # Read back the (possibly stamped) type via normal inference.
    return validator.infer_expression_type(expr)


def infer_range_expression_type(validator: 'TypeValidator', expr: 'RangeExpr') -> Optional[Type]:
    """Infer type of range expression - always returns Iterator<i32>.

    Range expressions always produce Iterator<i32> regardless of the
    start/end expression types. Any integer expressions are implicitly
    cast to i32 during code generation.

    Args:
        validator: The TypeValidator instance.
        expr: The range expression to infer.

    Returns:
        IteratorType with i32 element type.
    """
    # Always return Iterator<i32> for consistency with array iteration
    return IteratorType(element_type=BuiltinType.I32)


def int_literal_fits_in_type(value: int, target_type: BuiltinType) -> bool:
    """Check if an integer literal value fits in the target type's range.

    Args:
        value: The integer literal value
        target_type: The target numeric type

    Returns:
        True if the value fits in the type's range, False otherwise
    """
    # Define ranges for each integer type
    ranges = {
        BuiltinType.I8: (-128, 127),
        BuiltinType.I16: (-32768, 32767),
        BuiltinType.I32: (-2147483648, 2147483647),
        BuiltinType.I64: (-9223372036854775808, 9223372036854775807),
        BuiltinType.U8: (0, 255),
        BuiltinType.U16: (0, 65535),
        BuiltinType.U32: (0, 4294967295),
        BuiltinType.U64: (0, 18446744073709551615),
    }

    if target_type in ranges:
        min_val, max_val = ranges[target_type]
        return min_val <= value <= max_val

    # For non-integer types, don't apply range check
    return False


# Bit widths for the integer builtin types, used for radix bit-pattern range checks.
_INT_WIDTHS = {
    BuiltinType.I8: 8, BuiltinType.U8: 8,
    BuiltinType.I16: 16, BuiltinType.U16: 16,
    BuiltinType.I32: 32, BuiltinType.U32: 32,
    BuiltinType.I64: 64, BuiltinType.U64: 64,
}

# Largest finite magnitude representable in IEEE-754 single precision.
_F32_MAX = 3.4028234663852886e38


def int_literal_fits(value: int, radix: int, target_type: BuiltinType) -> bool:
    """Check whether an integer literal fits its context-typed target.

    Decimal literals use value ranges (signed/unsigned per type). Radix literals
    (hex/binary/octal) use bit-pattern semantics: any value fitting the type's bit
    width is legal, so `0xFF` is a valid `i8` (the 8-bit pattern -1). This mirrors
    the compiler's existing bare-i32 rule, now parameterized by width.
    """
    width = _INT_WIDTHS.get(target_type)
    if width is None:
        return False
    if radix == 10:
        return int_literal_fits_in_type(value, target_type)
    return 0 <= value <= (1 << width) - 1


def float_literal_fits(value: float, target_type: BuiltinType) -> bool:
    """Check whether a float literal fits its context-typed target.

    f64 holds any parsed literal; f32 rejects only overflow to infinity. Precision
    loss on narrowing (e.g. 0.1 -> f32) is silently rounded to nearest, matching Go
    and Rust.
    """
    if target_type == BuiltinType.F64:
        return True
    if target_type == BuiltinType.F32:
        return abs(value) <= _F32_MAX
    return False
