"""Enum definition collection."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import ERR

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.structs import StructTable, GenericStructTable
from sushi_lang.semantics.ast import EnumDef, Program
from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.predefined_types import PREDEFINED_ENUMS, predefined_enums
from sushi_lang.semantics.typesys import (
    EnumType,
    EnumVariantInfo,
)
from sushi_lang.semantics.generics.types import GenericEnumType

from sushi_lang.semantics.visibility import (
    VisibilityTable,
    library_clash_for_type_name,
    record_declaration,
    reject_library_clash,
)

from .utils import (
    extract_type_param_names, reject_duplicate_type_name, reject_reference_in,
    type_name_rules)


@dataclass
class EnumTable:
    """Table of enum types collected by the collect pass."""
    by_name: Dict[str, EnumType] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    spans: Dict[str, Optional[Span]] = field(default_factory=dict)
    # The unit each span is in, keyed alike: a duplicate is reported while ANOTHER
    # unit is being collected, so the note has to name this file (#473).
    files: Dict[str, Optional[str]] = field(default_factory=dict)
    # This compilation's auto-derived hash() and clone() (#601), read by name as
    # `SymbolTables.derived_methods`. It is stored HERE because the interning seams for
    # `Result` and `Maybe` derive a hash the moment they intern the enum and hold only
    # this table -- so the alternative was a parameter on all 33 of their call sites.
    derived: DerivedMethodTable = field(default_factory=DerivedMethodTable,
                                        compare=False, repr=False)


@dataclass
class GenericEnumTable:
    """Table of generic enum types collected by the collect pass."""
    by_name: Dict[str, GenericEnumType] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    spans: Dict[str, Optional[Span]] = field(default_factory=dict)
    # The unit each span is in, keyed alike: a duplicate is reported while ANOTHER
    # unit is being collected, so the note has to name this file (#473).
    files: Dict[str, Optional[str]] = field(default_factory=dict)


# The HOME of every predefined enum, derived from the one table that holds them
# (#574, Ruling 3). The `namespaces` pass reads the stamp off the `EnumType`; this view
# answers the same question by name.
PREDEFINED_ENUM_HOMES: dict[str, Optional[str]] = {
    predefined.name: predefined.home_module for predefined in PREDEFINED_ENUMS}


class EnumCollector:
    """Collector for enum definitions."""

    def __init__(
        self,
        reporter: Reporter,
        enums: EnumTable,
        generic_enums: GenericEnumTable,
        structs: 'StructTable',
        generic_structs: 'GenericStructTable',
    ) -> None:
        """Initialize enum collector."""
        self.r = reporter
        # The unit being collected. This pass shares one reporter across every
        # unit, so a record it stores has to remember its own file (#473).
        self.current_unit_file: Optional[str] = None
        self.current_unit_name: Optional[str] = None
        self.library_units: Set[str] = set()
        # The names this collector refused with CE3011 (#814): the analyzer stops on them.
        self.refused_library_types: list[str] = []
        self.visibility: Optional[VisibilityTable] = None
        self.enums = enums
        self.generic_enums = generic_enums
        self.structs = structs
        self.generic_structs = generic_structs

    def collect(self, root: Program) -> None:
        """Collect all enum definitions from program AST."""
        enums = root.enums
        if isinstance(enums, list):
            for enum in enums:
                if isinstance(enum, EnumDef):
                    self._collect_enum_def(enum)

    def register_predefined_enums(self) -> None:
        """The nine synthesized enums: in the table, in order, each with its stamp."""
        for enum in predefined_enums():
            self.enums.by_name[enum.name] = enum
            self.enums.order.append(enum.name)

    def _reject_library_clash(self, name: str, name_span: Optional[Span]) -> bool:
        """CE3011 when a library already took this name. True when it was refused."""
        clash = library_clash_for_type_name(
            self.visibility, name,
            current_unit=self.current_unit_name, library_units=self.library_units)
        if clash is None or clash.is_public:
            return False  # A public library type stays the plain duplicate (CE0004).
        reject_library_clash(self.r, clash, name_span, kind="enum", name=name,
                             filename=self.current_unit_file)
        self.refused_library_types.append(name)
        return True

    def _collect_enum_def(self, enum: EnumDef) -> None:
        """Collect enum definition and create EnumType or GenericEnumType."""
        name = enum.name
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = enum.name_span or enum.loc
        record_declaration(self.visibility, "enum", enum,
                           unit_name=self.current_unit_name,
                           filename=self.current_unit_file)

        type_params_raw = enum.type_params
        type_params: Optional[List[str]] = extract_type_param_names(type_params_raw)

        if reject_duplicate_type_name(self.r, "enum", name, name_span, type_name_rules(
            "enum", structs=self.structs, generic_structs=self.generic_structs,
            enums=self.enums, generic_enums=self.generic_enums,
        ), library_clash=self._reject_library_clash):
            return

        variants_list: List[EnumVariantInfo] = []
        variant_names: Set[str] = set()

        enum_variants = enum.variants
        for variant in enum_variants:
            variant_name = variant.name
            variant_types = variant.associated_types
            variant_loc = variant.loc

            if not isinstance(variant_name, str):
                continue

            if variant_name in variant_names:
                er.emit(self.r, ERR.CE2047, variant_loc, name=variant_name, enum_name=name)
                continue

            if variant_types is None:
                variant_types = []

            # A reference payload has no semantics -- the enum may outlive what it borrows
            # (CE2416, #316). Reported, then KEPT: dropping it would report a spurious arity
            # error at every construction. The variant's span carries it, there being no
            # per-payload one.
            for assoc_type in variant_types:
                reject_reference_in(self.r, assoc_type, variant_loc, ERR.CE2416)

            variant_names.add(variant_name)
            variants_list.append(EnumVariantInfo(
                name=variant_name,
                associated_types=tuple(variant_types)
            ))

        if type_params and len(type_params) > 0:
            type_param_instances = tuple(type_params_raw)

            generic_enum = GenericEnumType(
                name=name,
                type_params=type_param_instances,
                variants=tuple(variants_list)
            )

            self.generic_enums.order.append(name)
            self.generic_enums.by_name[name] = generic_enum
            self.generic_enums.spans[name] = name_span
            self.generic_enums.files[name] = self.current_unit_file
        else:
            enum_type = EnumType(
                name=name,
                variants=tuple(variants_list)
            )

            self.enums.order.append(name)
            self.enums.by_name[name] = enum_type
            self.enums.spans[name] = name_span
            self.enums.files[name] = self.current_unit_file
