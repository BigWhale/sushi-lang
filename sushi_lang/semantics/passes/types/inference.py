"""Type inference helpers for type validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import Type, BuiltinType, ArrayType, DynamicArrayType, IteratorType
from sushi_lang.semantics.ast import ArrayLiteral, IndexAccess, DynamicArrayFrom, Expr, RangeExpr
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from . import TypeValidator


def infer_array_literal_type(validator: 'TypeValidator', expr: ArrayLiteral) -> Optional[Type]:
    """Infer type of array literal based on elements (validates all elements match)."""
    if not expr.elements:
        return None

    first_element_type = validator.infer_expression_type(expr.elements[0])
    if first_element_type is None:
        return None

    # Verify all elements have the same type (CE2013)
    for _i, element in enumerate(expr.elements[1:], start=1):
        element_type = validator.infer_expression_type(element)
        if element_type is not None and element_type != first_element_type:
            er.emit(validator.reporter, er.ERR.CE2013, element.loc,
                   expected=display_type(first_element_type), got=display_type(element_type))

    return ArrayType(base_type=first_element_type, size=len(expr.elements))


def infer_index_access_type(validator: 'TypeValidator', expr: IndexAccess) -> Optional[Type]:
    """Infer type of array indexing - should return element type."""
    array_type = validator.infer_expression_type(expr.array)
    if array_type is None:
        return None

    if isinstance(array_type, (ArrayType, DynamicArrayType)):
        expr.inferred_element_type = array_type.base_type
        return array_type.base_type

    return None


def infer_dynamic_array_from_type(validator: 'TypeValidator', expr: DynamicArrayFrom, expected_type: Optional[DynamicArrayType] = None) -> Optional[Type]:
    """Infer type of from(array_literal) constructor from array literal elements."""
    array_literal = expr.elements
    if not array_literal.elements:
        return None

    expected_element_type = expected_type.base_type if expected_type else None

    first_element_type = infer_element_type_with_context(validator, array_literal.elements[0], expected_element_type)
    if first_element_type is None:
        return None

    for element in array_literal.elements[1:]:
        element_type = infer_element_type_with_context(validator, element, expected_element_type)
        if element_type != first_element_type:
            return None

    return DynamicArrayType(base_type=first_element_type)


def infer_element_type_with_context(validator: 'TypeValidator', expr: Expr, expected_type: Optional[Type]) -> Optional[Type]:
    """Infer type of an array element expression with optional contextual type hint."""
    # Context-type a bare numeric literal to the expected element type (stamps the
    # literal, range-checks it, emits CE2073 on overflow). Shares the single
    # propagation path so dynamic-array elements behave like every other context.
    if expected_type is not None and isinstance(expected_type, BuiltinType):
        from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
        propagate_types_to_value(validator, expr, expected_type)

    return validator.infer_expression_type(expr)


def infer_range_expression_type(validator: 'TypeValidator', expr: 'RangeExpr') -> Optional[Type]:
    """Infer type of range expression - always returns Iterator<i32>."""
    return IteratorType(element_type=BuiltinType.I32)


def int_literal_fits_in_type(value: int, target_type: BuiltinType) -> bool:
    """Check if an integer literal value fits in the target type's range."""
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

    return False


_INT_WIDTHS = {
    BuiltinType.I8: 8, BuiltinType.U8: 8,
    BuiltinType.I16: 16, BuiltinType.U16: 16,
    BuiltinType.I32: 32, BuiltinType.U32: 32,
    BuiltinType.I64: 64, BuiltinType.U64: 64,
}

_F32_MAX = 3.4028234663852886e38


def int_literal_fits(value: int, radix: int, target_type: BuiltinType) -> bool:
    """Check whether an integer literal fits its context-typed target."""
    width = _INT_WIDTHS.get(target_type)
    if width is None:
        return False
    if radix == 10:
        return int_literal_fits_in_type(value, target_type)
    return 0 <= value <= (1 << width) - 1


def float_literal_fits(value: float, target_type: BuiltinType) -> bool:
    """Check whether a float literal fits its context-typed target."""
    if target_type == BuiltinType.F64:
        return True
    if target_type == BuiltinType.F32:
        return abs(value) <= _F32_MAX
    return False
