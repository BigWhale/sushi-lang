"""Generic constraint validation for Sushi compiler."""

from typing import Optional
from sushi_lang.semantics.typesys import Type, BuiltinType, StructType, EnumType
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.semantics.passes.collect import PerkTable, PerkImplementationTable
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type


class ConstraintValidator:
    """Validates perk constraints on generic types."""

    def __init__(
        self,
        perk_table: PerkTable,
        perk_impl_table: PerkImplementationTable,
        reporter: Reporter
    ):
        """Initialize constraint validator."""
        self.perk_table = perk_table
        self.perk_impl_table = perk_impl_table
        self.reporter = reporter

    def validate_constraint(
        self,
        type_arg: Type,
        constraint_name: str,
        span: Optional['Span']
    ) -> bool:
        """Check if a type satisfies a single perk constraint."""
        type_name = self._get_type_name(type_arg)

        # Check if type implements the required perk
        if not self.perk_impl_table.implements(type_name, constraint_name):
            # `type_name` stays `<>` (the impl-table lookup key above); display `@()`.
            er.emit(self.reporter, er.ERR.CE4006, span,
                   type=display_type(type_arg), perk=constraint_name)
            return False

        return True

    def validate_all_constraints(
        self,
        bounded_param: BoundedTypeParam,
        type_arg: Type,
        span: Optional['Span']
    ) -> bool:
        """Validate all constraints on a type parameter."""
        # If no constraints, always valid
        if not bounded_param.constraints or len(bounded_param.constraints) == 0:
            return True

        # Validate each constraint
        all_valid = True
        for constraint in bounded_param.constraints:
            if not self.validate_constraint(type_arg, constraint, span):
                all_valid = False
                # Continue checking other constraints to report all errors

        return all_valid

    def _get_type_name(self, ty: Type) -> str:
        """Extract type name for lookup in implementation table."""
        if isinstance(ty, BuiltinType):
            return str(ty)
        elif isinstance(ty, (StructType, EnumType)):
            return ty.name
        else:
            # Fallback: use string representation
            return str(ty)
