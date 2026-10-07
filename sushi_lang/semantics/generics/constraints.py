"""Generic constraint validation for Sushi compiler."""

from typing import Optional
from sushi_lang.semantics.typesys import Type, holds_declared_resource
from sushi_lang.semantics.drop_set import drop_type_names
from sushi_lang.semantics.ast import BoundedTypeParam
from sushi_lang.semantics.passes.collect import (
    PerkTable, PerkImplementationTable, StructTable, EnumTable)
from sushi_lang.semantics.passes.collect.perks import PerkCollector
from sushi_lang.semantics.passes.resolve import table_resolver
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.contract_walk import perk_override_of, template_covers
from sushi_lang.semantics.generics.contracts import CONTRACTS, operand_contract
from sushi_lang.semantics.generics.hashing import hash_override_of, hashability_of
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
        pack_index: Optional[int] = None,
    ) -> bool:
        """Check if a type satisfies a single perk constraint.

        `pack_index` is the element's position in a type pack, and a refusal of a pack
        element takes the pack wording (CE2090) with the same note (#797).

        `span` and `filename` are the site that NAMES the refused instantiation, and
        `note` is `(span, filename)` of the constraint it violates (#579): the caret goes
        on the user's type, and the note on the `@(T: Loud)` that refused it, which may
        stand in another file -- a stdlib template's.

        "Does the perk EXIST" is asked first. A name no unit declares is not a contract
        any type can fail, and this answer arrives before the unit's own -- the
        whole-program stop below ends the analysis here -- so CE4006 named a perk the
        program has not got, and only an UNCALLED generic ever reached the CE4003 that
        says so (#703).
        """
        if self.perk_table.get(constraint_name) is None:
            return True

        if not (self.perk_impl_table.implements_type(type_arg, constraint_name)
                or template_covers(self.generic_perk_impls, type_arg, constraint_name)
                or self._derived_implements(type_arg, constraint_name)):
            if pack_index is None:
                diagnostic = er.emit_with(self.reporter, er.ERR.CE4006, span,
                                          filename=filename, type=display_type(type_arg),
                                          perk=constraint_name)
            else:
                diagnostic = er.emit_with(self.reporter, er.ERR.CE2090, span,
                                          filename=filename, index=pack_index,
                                          ty=display_type(type_arg), perk=constraint_name)
            note_span, note_file = note if note is not None else (None, None)
            if note_span is not None:
                diagnostic = diagnostic.note_at(
                    f"the constraint '{constraint_name}' is declared here", note_span, note_file)
            diagnostic.emit()
            return False

        return True

    def _derived_implements(self, type_arg: Type, constraint_name: str) -> bool:
        """Does the compiler derive this contract for the type? Then it satisfies it.

        `Hashable` (#696), the three contracts `Eq`, `Ord` and `Display`, and `Clone`,
        which every type satisfies unless it holds a resource (the CE2431 predicate).

        One predicate, the derive pass's own: `hashability_of`. A type it refuses -- a
        struct holding a `HashMap`, a `ptr`, a function value -- does not satisfy the
        constraint, unless an explicit `extend T with Hashable` answered above. A held
        type with such an implementation is the override and answers for itself (#891).
        """
        resolve = (table_resolver(self.struct_table, self.enum_table)
                   if self.struct_table is not None and self.enum_table is not None
                   else None)
        if constraint_name in CONTRACTS:
            # `Eq`, `Ord` and `Display` read the rule their operators read, so a
            # constraint never admits a type whose body would then refuse `==`.
            overridden = perk_override_of(constraint_name, self.perk_impl_table,
                                          self.generic_perk_impls)
            return operand_contract(type_arg, constraint_name, overridden=overridden,
                                    resolve=resolve)[0]
        if constraint_name == PerkCollector.CLONE_PERK:
            return not holds_declared_resource(
                type_arg, drop_type_names(self.perk_impl_table), resolve=resolve)
        if constraint_name != PerkCollector.HASHABLE_PERK:
            return False
        overridden = hash_override_of(self.perk_impl_table, self.generic_perk_impls)
        can_hash, _reason = hashability_of(type_arg, resolve=resolve, overridden=overridden)
        return can_hash

    def validate_all_constraints(
        self,
        bounded_param: BoundedTypeParam,
        type_arg: Type,
        span: Optional['Span'],
        filename: Optional[str] = None,
        note: Optional[tuple] = None,
        pack_index: Optional[int] = None,
    ) -> bool:
        """Validate all constraints on a type parameter."""
        if not bounded_param.constraints or len(bounded_param.constraints) == 0:
            return True

        all_valid = True
        for constraint in bounded_param.constraints:
            if not self.validate_constraint(type_arg, constraint, span, filename, note,
                                            pack_index):
                all_valid = False

        return all_valid
