"""
Generic constraint validation for Sushi compiler.

Validates that concrete types satisfy perk constraints when
generic functions/types are instantiated.

Example:
    fn compute_hash<T: Hashable>(T value) u64:
        return value.hash()

    # When called with Point:
    # - Validates that Point implements Hashable perk
    # - Emits CE4006 error if not
"""

from typing import Optional
from semantics.typesys import Type, BuiltinType, StructType, EnumType
from semantics.ast import BoundedTypeParam
from semantics.passes.collect import PerkTable, PerkImplementationTable
from internals.report import Reporter
from internals import errors as er


class ConstraintValidator:
    """Validates perk constraints on generic types.

    This class checks that concrete type arguments satisfy the perk
    constraints specified in generic type parameters.

    Example:
        Given: fn process<T: Hashable>(T value) ~:
        When: process(Point { x: 10, y: 20 })
        Check: Point implements Hashable
    """

    def __init__(
        self,
        perk_table: PerkTable,
        perk_impl_table: PerkImplementationTable,
        reporter: Reporter
    ):
        """Initialize constraint validator.

        Args:
            perk_table: Registry of all defined perks
            perk_impl_table: Registry of perk implementations
            reporter: Error reporter for constraint violations
        """
        self.perk_table = perk_table
        self.perk_impl_table = perk_impl_table
        self.reporter = reporter

    def validate_constraint(
        self,
        type_arg: Type,
        constraint_name: str,
        span: Optional['Span']
    ) -> bool:
        """Check if a type satisfies a single perk constraint.

        Args:
            type_arg: Concrete type being checked (e.g., i32, Point)
            constraint_name: Perk name (e.g., "Hashable")
            span: Source location for error reporting

        Returns:
            True if constraint is satisfied, False otherwise
        """
        type_name = self._get_type_name(type_arg)

        # Check if type implements the required perk
        if not self.perk_impl_table.implements(type_name, constraint_name):
            er.emit(self.reporter, er.ERR.CE4006, span,
                   type=type_name, perk=constraint_name)
            return False

        return True

    def validate_all_constraints(
        self,
        bounded_param: BoundedTypeParam,
        type_arg: Type,
        span: Optional['Span']
    ) -> bool:
        """Validate all constraints on a type parameter.

        Example: T: Hashable + Eq
        Checks that type_arg implements both Hashable and Eq.

        Args:
            bounded_param: Type parameter with constraints
            type_arg: Concrete type to validate
            span: Source location for error reporting

        Returns:
            True if all constraints satisfied, False otherwise
        """
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
        """Extract type name for lookup in implementation table.

        Args:
            ty: Type object to extract name from

        Returns:
            String representation of type name
        """
        if isinstance(ty, BuiltinType):
            return str(ty)
        elif isinstance(ty, (StructType, EnumType)):
            return ty.name
        else:
            # Fallback: use string representation
            return str(ty)
