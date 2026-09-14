"""Generic constraint validation for Sushi compiler."""

from typing import Optional
from sushi_lang.semantics.typesys import Type, BuiltinType, StructType, EnumType
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.semantics.passes.collect import (
    PerkTable, PerkImplementationTable, StructTable, EnumTable)
from sushi_lang.semantics.passes.collect.perks import PerkCollector
from sushi_lang.semantics.passes.resolve import table_resolver
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.hashing import hashability_of
from sushi_lang.semantics.generics.type_display import display_type


class ConstraintValidator:
    """Validates perk constraints on generic types."""

    def __init__(
        self,
        perk_table: PerkTable,
        perk_impl_table: PerkImplementationTable,
        reporter: Reporter,
        generic_perk_impls=None,
        struct_table: Optional[StructTable] = None,
        enum_table: Optional[EnumTable] = None,
    ):
        """Initialize constraint validator."""
        self.perk_table = perk_table
        self.perk_impl_table = perk_impl_table
        self.reporter = reporter
        # The generic-target implementation templates (`GenericPerkImplTable`), when the
        # caller has them: a template answers for an instantiation whose copy is not cut yet.
        self.generic_perk_impls = generic_perk_impls
        # The struct and enum tables, for the derived answer (#696): the walk that says
        # whether a type has a derived hash runs here BEFORE the resolve pass, so it
        # resolves each written name against the tables as it goes.
        self.struct_table = struct_table
        self.enum_table = enum_table

    def validate_constraint(
        self,
        type_arg: Type,
        constraint_name: str,
        span: Optional['Span'],
        filename: Optional[str] = None,
        note: Optional[tuple] = None,
    ) -> bool:
        """Check if a type satisfies a single perk constraint.

        `span` and `filename` are the site that NAMES the refused instantiation, and
        `note` is `(span, filename)` of the constraint it violates (#579): the caret goes
        on the user's type, and the note on the `@(T: Loud)` that refused it, which may
        stand in another file -- a stdlib template's.
        """
        type_name = self._get_type_name(type_arg)

        if not (self.perk_impl_table.implements(type_name, constraint_name)
                or self._template_implements(type_arg, constraint_name)
                or self._derived_implements(type_arg, constraint_name)):
            diagnostic = er.emit_with(self.reporter, er.ERR.CE4006, span, filename=filename,
                                      type=display_type(type_arg), perk=constraint_name)
            note_span, note_file = note if note is not None else (None, None)
            if note_span is not None:
                diagnostic = diagnostic.note(
                    f"the constraint '{constraint_name}' is declared here", note_span, note_file)
            diagnostic.emit()
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

    def _derived_implements(self, type_arg: Type, constraint_name: str) -> bool:
        """Does the derive pass hash this type? Then it satisfies `Hashable` (#696).

        One predicate, the derive pass's own: `hashability_of`. A type it refuses -- a
        struct holding a `HashMap`, a `ptr`, a function value -- does not satisfy the
        constraint, unless an explicit `extend T with Hashable` answered above.
        """
        if constraint_name != PerkCollector.HASHABLE_PERK:
            return False
        resolve = (table_resolver(self.struct_table, self.enum_table)
                   if self.struct_table is not None and self.enum_table is not None
                   else None)
        can_hash, _reason = hashability_of(type_arg, resolve=resolve)
        return can_hash

    def validate_all_constraints(
        self,
        bounded_param: BoundedTypeParam,
        type_arg: Type,
        span: Optional['Span'],
        filename: Optional[str] = None,
        note: Optional[tuple] = None,
    ) -> bool:
        """Validate all constraints on a type parameter."""
        if not bounded_param.constraints or len(bounded_param.constraints) == 0:
            return True

        all_valid = True
        for constraint in bounded_param.constraints:
            if not self.validate_constraint(type_arg, constraint, span, filename, note):
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
