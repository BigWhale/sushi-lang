"""Type compatibility checking for type validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics import array_runs
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import Type, BuiltinType, UnknownType, ArrayType, DynamicArrayType, ReferenceType, BorrowMode
from sushi_lang.semantics.ast import Expr, ArrayLiteral, DynamicArrayNew, DynamicArrayFrom
from sushi_lang.semantics.type_resolution import resolve_unknown_type, TypeResolver
from .inference import infer_dynamic_array_from_type

if TYPE_CHECKING:
    from . import TypeValidator


def validate_assignment_compatibility(validator: 'TypeValidator', declared_type: Optional[Type], value_expr: Expr, declared_span: Optional[Span], value_span: Optional[Span]) -> None:
    """Validate that value expression type matches declared type (CE2002)."""
    if declared_type is None:
        return  # Can't validate without declared type

    validator.validate_expression(value_expr)

    if isinstance(declared_type, ArrayType) and isinstance(value_expr, ArrayLiteral):
        if reject_array_size_mismatch(validator, declared_type, value_expr, value_span):
            return

    if isinstance(declared_type, DynamicArrayType):
        if isinstance(value_expr, DynamicArrayNew):
            return
        elif isinstance(value_expr, DynamicArrayFrom):
            inferred_type = infer_dynamic_array_from_type(validator, value_expr, expected_type=declared_type)
            if inferred_type is None:
                return  # Error already reported or empty array
            if not types_compatible(validator, inferred_type, declared_type):
                b = er.emit_with(validator.reporter, er.ERR.CE2002, value_span,
                       got=display_type(inferred_type), expected=display_type(declared_type))
                if declared_span:
                    b.note("declared here", declared_span)
                b.emit()
            return

    value_type = validator.infer_expression_type(value_expr)
    if value_type is None:
        return  # Can't validate without inferred type

    if not types_compatible(validator, value_type, declared_type):
        b = er.emit_with(validator.reporter, er.ERR.CE2002, value_span,
               got=display_type(value_type), expected=display_type(declared_type))
        if declared_span:
            b.note("declared here", declared_span)
        b.emit()


def reject_array_size_mismatch(validator: 'TypeValidator', declared_type: ArrayType,
                               literal: ArrayLiteral, value_span: Optional[Span]) -> bool:
    """CE2011: the EXPANDED element count must match the declared size.

    A repeated element is written by length, so the compiler cannot know which run is
    short -- either of them could be. It lists what it does know instead: every run with
    the absolute span it fills. A reader who knows a boundary sees which run misses it.
    """
    runs = array_runs.read_runs(
        literal.elements,
        array_runs.const_int_reader(validator.const_table, validator.ast_constants,
                                    validator.current_unit_name),
        Reporter())
    if runs is None:
        return True  # a bad count. CE2017 said so; a size report would pile on.

    # A fixed array's length is part of its TYPE, so this caller needs a number (Ruling 3).
    # The readable-length diagnostic speaks first and CE2011 stays silent after it, the way
    # it already stays silent after CE2017.
    got = array_runs.require_readable_length(runs, validator.reporter)
    if got is None:
        return True
    if got == declared_type.size:
        return False

    b = er.emit_with(validator.reporter, er.ERR.CE2011, value_span,
                     got=got, expected=declared_type.size)
    if array_runs.has_run(literal.elements):
        for number, run in enumerate(runs, start=1):
            b.note(f"run {number} fills {run.start}..{run.end} "
                   f"({run.count} element{'' if run.count == 1 else 's'})", run.loc)
    b.emit()
    return True


def validate_return_compatibility(validator: 'TypeValidator', expected_type: Type, return_expr: Expr, return_span: Optional[Span]) -> None:
    """Validate that return expression type matches function return type (CE2003)."""
    validator.validate_expression(return_expr)

    actual_type = validator.infer_expression_type(return_expr)
    if actual_type is None:
        return  # Can't validate without inferred type

    if not types_compatible(validator, actual_type, expected_type):
        er.emit(validator.reporter, er.ERR.CE2003, return_span,
               got=display_type(actual_type), expected=display_type(expected_type))


def resolve_generic_type_ref(validator: 'TypeValidator', ty: Type) -> Type:
    """Resolve GenericTypeRef to monomorphized EnumType or StructType."""
    resolver = TypeResolver(validator.struct_table.by_name, validator.enum_table.by_name)
    return resolver.resolve_generic_type_ref(ty)


def compare_resolved_types(validator: 'TypeValidator', actual: Type, expected: Type) -> bool:
    """Compare two resolved types (no GenericTypeRef or UnknownType resolution)."""
    from sushi_lang.semantics.typesys import DynamicArrayType, ArrayType

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
        return (types_compatible(validator, actual.ok_type, expected.ok_type) and
                types_compatible(validator, actual.err_type, expected.err_type))

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

    resolved_actual = resolve_generic_type_ref(validator, actual)
    resolved_expected = resolve_generic_type_ref(validator, expected)

    return compare_resolved_types(validator, resolved_actual, resolved_expected)


def is_valid_cast(source_type: Type, target_type: Type) -> bool:
    """Check if a cast from source_type to target_type is valid."""
    if source_type == target_type:
        return True

    # Only allow casts between numeric types for now
    # Casts are explicit only: there is no implicit numeric conversion
    numeric_types = {
        BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
        BuiltinType.F32, BuiltinType.F64
    }

    if source_type in numeric_types and target_type in numeric_types:
        return True

    return False
