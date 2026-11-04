"""Type compatibility checking for type validation.

Port templates: `validate_assignment_compatibility` call
`TypeValidator.validate_expression`, and that API is deleted. No pass reaches them
today. Group B8 (the reference `let`) ports them to SHIR inputs before a SHIR pass calls
them.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals.report import Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import Type, UnknownType, ArrayType, DynamicArrayType, ReferenceType, BorrowMode, IteratorType
from sushi_lang.semantics.ast import Expr, DynamicArrayNew, DynamicArrayFrom
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.type_predicates import BUILTIN_NUMERIC_TYPES
from .inference import infer_dynamic_array_from_type

if TYPE_CHECKING:
    from . import TypeValidator


def validate_assignment_compatibility(validator: 'TypeValidator', declared_type: Optional[Type], value_expr: Expr, declared_span: Optional[Span], value_span: Optional[Span]) -> None:
    """Validate the value, then that its type matches the declared one (CE2002)."""
    if declared_type is None:
        return  # Can't validate without declared type

    validator.validate_expression(value_expr)
    reject_incompatible_assignment(validator, declared_type, value_expr,
                                   declared_span, value_span)


def reject_incompatible_assignment(validator: 'TypeValidator', declared_type: Optional[Type], value_expr: Expr, declared_span: Optional[Span], value_span: Optional[Span]) -> bool:
    """CE2002 alone: the value has been validated already.

    Split from the validation because a caller can have a better code for the SAME
    binding and still owes the value its own diagnostics -- an unhandled `Result` is
    CE2505, and the call that produced it may hold a wrong argument the reader needs
    to see (#535). Answers whether a diagnostic refused the binding.
    """
    if declared_type is None:
        return False

    if isinstance(declared_type, DynamicArrayType) and isinstance(value_expr, DynamicArrayNew):
        return False
    if isinstance(declared_type, DynamicArrayType) and isinstance(value_expr, DynamicArrayFrom):
        value_type = infer_dynamic_array_from_type(validator, value_expr, expected_type=declared_type)
    else:
        value_type = validator.infer_expression_type(value_expr)
    return reject_mismatch(validator, value_type, declared_type, value_span, declared_span)


def reject_unhandled_result(validator: 'TypeValidator', got: Optional[Type], expected: Type,
                            span: Optional[Span]) -> bool:
    """CE2505 when a value of a `Result` type goes where a type that is no `Result` is
    declared (#535). Asked before CE2002, which refuses the same value and names no fix."""
    from sushi_lang.semantics.generics.results import is_result_enum
    if not is_result_enum(got) or is_result_enum(expected):
        return False
    er.emit(validator.reporter, er.ERR.CE2505, span)
    return True


def reject_mismatch(validator: 'TypeValidator', got: Optional[Type], expected: Type,
                    value_span: Optional[Span], declared_span: Optional[Span]) -> bool:
    """CE2002 when a value of the type `got` cannot go where `expected` is declared.

    A note points at the declared type, when the position writes one. No type (`got` is
    None) is an error that is already reported, and nothing more is said. Answers whether
    a diagnostic refused the value.
    """
    if got is None or types_compatible(validator, got, expected):
        return False
    b = er.emit_with(validator.reporter, er.ERR.CE2002, value_span,
           got=display_type(got), expected=display_type(expected))
    if declared_span:
        b.note_at("declared here", declared_span)
    b.emit()
    return True


def reject_return_mismatch(validator: 'TypeValidator', got: Optional[Type], expected: Type,
                           span: Optional[Span]) -> bool:
    """CE2003 when a returned value of the type `got` does not match the return type."""
    if got is None or types_compatible(validator, got, expected):
        return False
    er.emit(validator.reporter, er.ERR.CE2003, span,
            got=display_type(got), expected=display_type(expected))
    return True


def resolve_generic_type_ref(validator: 'TypeValidator', ty: Type) -> Type:
    """The instance a WRITTEN generic names, and nothing else touched.

    Narrower than `resolve_declared_type` on purpose: `compare_resolved_types` reads two
    `UnknownType`s by NAME and resolves a one-sided one itself, so resolving a name here
    would answer that question a second time and from one side only.
    """
    from sushi_lang.semantics.generics.types import GenericTypeRef

    from .utils import resolve_declared_type

    if not isinstance(ty, GenericTypeRef):
        return ty
    resolved = resolve_declared_type(validator, ty)
    return resolved if resolved is not None else ty


def compare_resolved_types(validator: 'TypeValidator', actual: Type, expected: Type) -> bool:
    """Compare two resolved types (no GenericTypeRef or UnknownType resolution)."""
    from sushi_lang.semantics.typesys import DynamicArrayType

    if actual == expected:
        return True

    if isinstance(actual, UnknownType) and isinstance(expected, UnknownType):
        return actual.name == expected.name

    if isinstance(actual, UnknownType):
        resolved = resolve_unknown_type(actual, validator.struct_table.by_name, validator.enum_table.by_name)
        return resolved == expected

    if isinstance(expected, UnknownType):
        resolved = resolve_unknown_type(expected, validator.struct_table.by_name, validator.enum_table.by_name)
        return actual == resolved

    if isinstance(actual, DynamicArrayType) and isinstance(expected, DynamicArrayType):
        return types_compatible(validator, actual.base_type, expected.base_type)

    if isinstance(actual, ArrayType) and isinstance(expected, ArrayType):
        return actual.size == expected.size and types_compatible(validator, actual.base_type, expected.base_type)

    return False


def _params_compatible(validator: 'TypeValidator', actual: Type, expected: Type) -> bool:
    """Compatibility for what one parameter INSIDE a function type carries.

    The MODE is a separate question, answered once by the caller off `FunctionType.modes`.
    """
    if isinstance(actual, ReferenceType) and isinstance(expected, ReferenceType):
        return types_compatible(validator, actual.referenced_type, expected.referenced_type)
    return types_compatible(validator, actual, expected)


def types_compatible(validator: 'TypeValidator', actual: Type, expected: Type) -> bool:
    """Check if two types are compatible (handles UnknownType resolution to StructType/EnumType).
    """
    from sushi_lang.semantics.typesys import FunctionType

    if actual == expected:
        return True

    # A refused type is one fault (#991): CE2001 was its diagnostic, and a compare against
    # it answers yes so that no position reports the same fault again.
    from .utils import names_no_type
    if names_no_type(validator, actual) or names_no_type(validator, expected):
        return True

    # Function-value compatibility: invariant on arity, every parameter, ok type, and
    # err type (no variance in v1). Recurse so members still carrying UnknownType resolve.
    if isinstance(actual, FunctionType) and isinstance(expected, FunctionType):
        if len(actual.param_types) != len(expected.param_types):
            return False
        # Invariant in the MODE too, in both directions (borrow-model.md S7). `peek` and
        # `poke` ride on the parameter's type, so they were already compared here; `nom`
        # does not, and went uncompared -- a `nom` callee satisfied a borrow fn type and
        # the call was a double free (#368). `modes` is the one normalized answer.
        if actual.modes != expected.modes:
            return False
        if not all(_params_compatible(validator, ap, ep)
                   for ap, ep in zip(actual.param_types, expected.param_types, strict=False)):
            return False
        if (actual.err_type is None) != (expected.err_type is None):
            return False
        return (types_compatible(validator, actual.ok_type, expected.ok_type) and
                (actual.err_type is None
                 or types_compatible(validator, actual.err_type, expected.err_type)))

    # Reference type compatibility with coercion
    # - poke T can be passed where peek T is expected (safe downgrade)
    # - peek T cannot be passed where poke T is expected
    if isinstance(actual, ReferenceType) and isinstance(expected, ReferenceType):
        if not types_compatible(validator, actual.referenced_type, expected.referenced_type):
            return False

        if actual.mutability == expected.mutability:
            return True  # Same mutability
        elif actual.mutability == BorrowMode.POKE and expected.mutability == BorrowMode.PEEK:
            return True  # poke -> peek coercion allowed
        else:
            return False  # peek -> poke not allowed

    if isinstance(actual, IteratorType) and isinstance(expected, IteratorType):
        return _iterators_compatible(validator, actual, expected)

    resolved_actual = resolve_generic_type_ref(validator, actual)
    resolved_expected = resolve_generic_type_ref(validator, expected)

    return compare_resolved_types(validator, resolved_actual, resolved_expected)


def _iterators_compatible(validator: 'TypeValidator', actual: IteratorType,
                          expected: IteratorType) -> bool:
    """One item, one mode. Each side is in its one form first: a plain item is one type."""
    from .utils import resolve_declared_type

    settled_actual = resolve_declared_type(validator, actual)
    settled_expected = resolve_declared_type(validator, expected)
    if not (isinstance(settled_actual, IteratorType)
            and isinstance(settled_expected, IteratorType)):
        return False
    return (settled_actual.peeks == settled_expected.peeks
            and types_compatible(validator, settled_actual.element_type,
                                 settled_expected.element_type))


def is_valid_cast(source_type: Type, target_type: Type) -> bool:
    """Check if a cast from source_type to target_type is valid.

    An identity cast is a no-op and stays legal. A cast between two error types calls the
    declared conversion of the pair, which the caller asks `conversions.find_conversion`
    for (docs/design/error-conversion.md section 3.2).
    """
    if source_type == target_type:
        return True

    # Only allow casts between numeric types for now
    # Casts are explicit only: there is no implicit numeric conversion
    if (source_type in BUILTIN_NUMERIC_TYPES
            and target_type in BUILTIN_NUMERIC_TYPES):
        return True

    return False
