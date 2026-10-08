"""Generic constraint validation for Sushi compiler."""

from contextlib import contextmanager
from typing import Callable, Iterator, Optional, Sequence, Set, Tuple
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


#: "Does this type satisfy this perk?" -- what a target bound asks of one argument.
BoundHolds = Callable[[Type, str], bool]


def target_bounds_hold(bounds: Sequence[BoundedTypeParam], type_args: Sequence[Type],
                       holds: BoundHolds) -> bool:
    """Does each type argument satisfy the target bound of its position? No diagnostic.

    The ONE answer to "does this extension or perk template apply to this instance"
    (#1070, R1): every cutter, every override and the call-site rung read it, so a
    derived override never names a method that has no copy. `holds` is
    `ConstraintValidator.holds_bound`. A template with no target bound asks nothing.
    The bounds and the arguments are index-aligned: a count that differs is a fault of
    the compiler, never an answer.
    """
    if len(bounds) != len(type_args):
        from sushi_lang.internals.errors import raise_internal_error
        raise_internal_error("CE0015", message=(
            f"{len(bounds)} target bounds against {len(type_args)} type arguments"))
    return all(holds(arg, perk)
               for bound, arg in zip(bounds, type_args, strict=True)
               for perk in bound.constraints or ())


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
        # The (type, perk) questions in progress. A template's target bound asks of the
        # type that its own answer is part of when the type is recursive, and that
        # question is answered yes while it is open: the other positions decide.
        self._asking: Set[Tuple[str, str]] = set()

    def satisfies(self, type_arg: Type, constraint_name: str) -> bool:
        """Does the type meet the constraint? No diagnostic: the one predicate."""
        key = (str(type_arg), constraint_name)
        if key in self._asking:
            return True
        self._asking.add(key)
        try:
            return (self.perk_impl_table.implements_type(type_arg, constraint_name)
                    or template_covers(self.generic_perk_impls, type_arg, constraint_name,
                                       self.holds_bound)
                    or self._derived_implements(type_arg, constraint_name))
        finally:
            self._asking.discard(key)

    @contextmanager
    def reporting_to(self, reporter: Reporter) -> Iterator[None]:
        """Emit to `reporter` for the length of one check of a site."""
        saved, self.reporter = self.reporter, reporter
        try:
            yield
        finally:
            self.reporter = saved

    def holds_bound(self, type_arg: Type, constraint_name: str) -> bool:
        """Does the type satisfy a bound? A perk that no unit declares holds (#1070).

        CE4003 is the one fault of such a bound, as of a constraint
        (`validate_constraint`).
        """
        return (self.perk_table.get(constraint_name) is None
                or self.satisfies(type_arg, constraint_name))

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

        if not self.satisfies(type_arg, constraint_name):
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
            # A type parameter of a template under check passes the constraint on only
            # when a constraint of its own promises it (#1070).
            from sushi_lang.semantics.generics.opaque import explain_unpromised
            at_fault = (self._clone_resources(type_arg)
                        if constraint_name == PerkCollector.CLONE_PERK else None)
            explain_unpromised(diagnostic, type_arg, constraint_name, at_fault).emit()
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
                                          self.generic_perk_impls, self.holds_bound)
            return operand_contract(type_arg, constraint_name, overridden=overridden,
                                    resolve=resolve)[0]
        if constraint_name == PerkCollector.CLONE_PERK:
            return not self._clone_resources(type_arg)
        if constraint_name != PerkCollector.HASHABLE_PERK:
            return False
        overridden = hash_override_of(self.perk_impl_table, self.generic_perk_impls,
                                      self.holds_bound)
        can_hash, _reason = hashability_of(type_arg, resolve=resolve, overridden=overridden)
        return can_hash

    def _clone_resources(self, type_arg: Type) -> list:
        """The types in `type_arg` that declare a resource: `Clone` holds when none does.

        The one predicate, `holds_declared_resource`, with its `found` list, so the refusal
        names the type parameter at fault (#1070).
        """
        resolve = (table_resolver(self.struct_table, self.enum_table)
                   if self.struct_table is not None and self.enum_table is not None
                   else None)
        found: list = []
        holds_declared_resource(type_arg, drop_type_names(self.perk_impl_table),
                                resolve=resolve, found=found)
        return found

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
