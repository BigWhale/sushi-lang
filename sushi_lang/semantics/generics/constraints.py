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
        reporter: Reporter,
        generic_perk_impls=None,
    ):
        """Initialize constraint validator."""
        self.perk_table = perk_table
        self.perk_impl_table = perk_impl_table
        self.reporter = reporter
        # The generic-target implementation templates (`GenericPerkImplTable`), when the
        # caller has them: a template answers for an instantiation whose copy is not cut yet.
        self.generic_perk_impls = generic_perk_impls

    def validate_constraint(
        self,
        type_arg: Type,
        constraint_name: str,
        span: Optional['Span']
    ) -> bool:
        """Check if a type satisfies a single perk constraint."""
        type_name = self._get_type_name(type_arg)

        if not (self.perk_impl_table.implements(type_name, constraint_name)
                or self._template_implements(type_arg, constraint_name)):
            er.emit(self.reporter, er.ERR.CE4006, span,
                   type=display_type(type_arg), perk=constraint_name)
            return False

        return True

    def _template_implements(self, type_arg: Type, constraint_name: str) -> bool:
        """Does a GENERIC-target implementation cover this instantiation (#555)?

        `extend Box@(T) with Show` applies to every `Box@(...)` by construction, and the
        copy for a LATE instantiation is cut only after the functions are monomorphized
        -- so the table cannot answer yet, while the template already can. Without this
        the answer depended on the order the copies were cut in.
        """
        templates = self.generic_perk_impls
        base = getattr(type_arg, "generic_base", None)
        args = getattr(type_arg, "generic_args", None)
        if templates is None or not base or not args:
            return False
        return any(template.impl.perk_name == constraint_name
                   and len(template.type_params) == len(args)
                   for template in templates.templates(base))

    def validate_all_constraints(
        self,
        bounded_param: BoundedTypeParam,
        type_arg: Type,
        span: Optional['Span']
    ) -> bool:
        """Validate all constraints on a type parameter."""
        if not bounded_param.constraints or len(bounded_param.constraints) == 0:
            return True

        all_valid = True
        for constraint in bounded_param.constraints:
            if not self.validate_constraint(type_arg, constraint, span):
                all_valid = False

        return all_valid

    def _get_type_name(self, ty: Type) -> str:
        """Extract type name for lookup in implementation table."""
        if isinstance(ty, BuiltinType):
            return str(ty)
        elif isinstance(ty, (StructType, EnumType)):
            return ty.name
        else:
            return str(ty)
