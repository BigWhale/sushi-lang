"""The collect pass -- orchestrates every collector through a facade."""

from __future__ import annotations
from typing import Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.tables import SymbolTables

from sushi_lang.internals.report import Origin, Reporter
from sushi_lang.semantics.ast import Program
from sushi_lang.semantics.typesys import (
    Type,
    BuiltinType,
    EnumVariantInfo,
    PointerType,
)
from sushi_lang.semantics.generics.types import (
    TypeParameter,
    GenericEnumType,
    GenericStructType,
)

from .constants import ConstantCollector, ConstantTable, ConstSig
from .structs import StructCollector, StructTable, GenericStructTable
from .enums import EnumCollector, EnumTable, GenericEnumTable
from .functions import (
    FunctionCollector,
    FunctionTable,
    GenericFunctionTable,
    ExtensionTable,
    GenericExtensionTable,
    FuncSig,
    GenericFuncDef,
    Param,
    ExtensionMethod,
    GenericExtensionMethod,
)
from .perks import PerkCollector, PerkTable, PerkImplementationTable
from .externals import ExternalCollector, ExternalTable, ExternalSig
from .utils import extract_type_param_names
from sushi_lang.semantics.visibility import VisibilityTable, record_declarations

__all__ = [
    'CollectorPass',
    'ConstantTable',
    'StructTable',
    'GenericStructTable',
    'EnumTable',
    'GenericEnumTable',
    'FunctionTable',
    'GenericFunctionTable',
    'ExtensionTable',
    'GenericExtensionTable',
    'PerkTable',
    'PerkImplementationTable',
    'ExternalTable',
    'ConstSig',
    'ExternalSig',
    'FuncSig',
    'GenericFuncDef',
    'Param',
    'ExtensionMethod',
    'GenericExtensionMethod',
    'extract_type_param_names',
]


class CollectorPass:
    """Collect constants, structs, enums, functions, and perks from the AST."""

    def __init__(self, reporter: Reporter,
                 library_units: Optional[Set[str]] = None) -> None:
        """Initialize collector pass with all sub-collectors.

        `library_units` names the units that arrived from a source library, so a
        consumer definition of the same name shadows theirs instead of colliding.
        """
        self.r = reporter

        self.constants = ConstantTable()
        self.structs = StructTable()
        self.generic_structs = GenericStructTable()
        self.enums = EnumTable()
        self.generic_enums = GenericEnumTable()
        self.funcs = FunctionTable()
        self.generic_funcs = GenericFunctionTable()
        self.extensions = ExtensionTable()
        self.generic_extensions = GenericExtensionTable()
        self.perks = PerkTable()
        self.perk_impls = PerkImplementationTable()
        self.externals = ExternalTable()
        self.visibility = VisibilityTable()

        self.known_types: Set[Type] = {
            BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
            BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
            BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
        }

        self.constant_collector = ConstantCollector(
            reporter=reporter,
            constants=self.constants
        )

        self.struct_collector = StructCollector(
            reporter=reporter,
            structs=self.structs,
            generic_structs=self.generic_structs,
            known_types=self.known_types
        )

        self.enum_collector = EnumCollector(
            reporter=reporter,
            enums=self.enums,
            generic_enums=self.generic_enums,
            structs=self.structs,
            generic_structs=self.generic_structs,
            known_types=self.known_types
        )

        self.perk_collector = PerkCollector(
            reporter=reporter,
            perks=self.perks,
            perk_impls=self.perk_impls
        )

        self.external_collector = ExternalCollector(
            reporter=reporter,
            externals=self.externals
        )

        self.function_collector = FunctionCollector(
            reporter=reporter,
            funcs=self.funcs,
            generic_funcs=self.generic_funcs,
            extensions=self.extensions,
            generic_extensions=self.generic_extensions,
            structs=self.structs,
            enums=self.enums,
            generic_structs=self.generic_structs,
            generic_enums=self.generic_enums
        )
        # Which units came from a library, for every collector that has to know.
        for collector in (self.struct_collector, self.enum_collector,
                          self.perk_collector, self.function_collector):
            collector.library_units = set(library_units or ())

        # And who declared what, for the four that ask it: three refuse a library clash
        # with CE3011, and all four refuse a promise about a private perk with CE4011. A
        # struct table carries a file and not a unit, so the answer comes from here.
        for collector in (self.struct_collector, self.enum_collector,
                          self.function_collector, self.perk_collector):
            collector.visibility = self.visibility

        self._register_predefined_structs()
        self._register_predefined_enums()
        self._register_predefined_generics()

    def run(self, root: Program, unit_name: Optional[str] = None,
            unit_file: Optional[str] = None) -> 'SymbolTables':
        """Run all collection passes in dependency order."""
        # This pass walks every unit through ONE reporter, unlike the per-unit passes,
        # which build their own. Naming the unit here is what keeps a span from being
        # rendered against the entry file, and it answers for every emit site in the
        # pass at once (#473). The collectors below carry the file too, because a
        # "first defined here" note can point into a unit that is no longer current.
        previous_origin = self.r.origin
        if unit_file is not None:
            self.r.origin = Origin(filename=unit_file)
        try:
            return self._collect(root, unit_name, unit_file)
        finally:
            self.r.origin = previous_origin

    def _collect(self, root: Program, unit_name: Optional[str],
                 unit_file: Optional[str]) -> 'SymbolTables':
        # One way in for all six: the fields, not a parameter on one collector's method.
        for collector in (self.constant_collector, self.struct_collector,
                          self.enum_collector, self.perk_collector,
                          self.external_collector, self.function_collector):
            collector.current_unit_file = unit_file
            collector.current_unit_name = unit_name

        self.constant_collector.collect(root)
        self.struct_collector.collect(root)
        self.enum_collector.collect(root)
        self.perk_collector.collect_definitions(root)
        self.perk_collector.collect_implementations(root)
        self.perk_collector.register_synthetic_impls()
        self.function_collector.collect_functions(root)
        self.function_collector.collect_extensions(root)
        self.function_collector.register_stdlib_functions(root)
        self.external_collector.collect(root)

        record_declarations(self.visibility, root,
                            unit_name=unit_name, filename=unit_file)

        from sushi_lang.semantics.tables import SymbolTables
        return SymbolTables(
            constants=self.constants,
            structs=self.structs,
            enums=self.enums,
            generic_enums=self.generic_enums,
            generic_structs=self.generic_structs,
            perks=self.perks,
            perk_impls=self.perk_impls,
            funcs=self.funcs,
            extensions=self.extensions,
            generic_extensions=self.generic_extensions,
            generic_funcs=self.generic_funcs,
            externals=self.externals,
            visibility=self.visibility,
        )

    def _register_predefined_structs(self) -> None:
        """Register predefined structs (ProcessOutput, etc.)."""
        self.struct_collector.register_predefined_structs()

    def _register_predefined_enums(self) -> None:
        """Register predefined enums (FileMode, FileResult, etc.)."""
        self.enum_collector.register_predefined_enums()

    def _register_predefined_generics(self) -> None:
        """Register predefined generic enums and structs."""
        # Result<T, E>: Ok(T) / Err(E).
        result_generic = GenericEnumType(
            name="Result",
            type_params=(TypeParameter(name="T"), TypeParameter(name="E")),
            variants=(
                EnumVariantInfo(
                    name="Ok",
                    associated_types=(TypeParameter(name="T"),)
                ),
                EnumVariantInfo(
                    name="Err",
                    associated_types=(TypeParameter(name="E"),)
                ),
            )
        )
        self.generic_enums.by_name["Result"] = result_generic
        self.generic_enums.order.append("Result")

        # Maybe<T>: Some(T) / None().
        maybe_generic = GenericEnumType(
            name="Maybe",
            type_params=(TypeParameter(name="T"),),
            variants=(
                EnumVariantInfo(
                    name="Some",
                    associated_types=(TypeParameter(name="T"),)
                ),
                EnumVariantInfo(
                    name="None",
                    # None variant has no associated data
                    associated_types=()
                ),
            )
        )
        self.generic_enums.by_name["Maybe"] = maybe_generic
        self.generic_enums.order.append("Maybe")

        # Own<T>: unique ownership of a heap T. The field is really a PointerType.
        own_generic = GenericStructType(
            name="Own",
            type_params=(TypeParameter(name="T"),),
            fields=(("value", PointerType(pointee_type=TypeParameter(name="T"))),)
        )
        self.generic_structs.by_name["Own"] = own_generic
        self.generic_structs.order.append("Own")

        from sushi_lang.semantics.generics.active_generics import is_generic_active
        from sushi_lang.semantics.generics.hashmap import hashmap_generic_struct
        if is_generic_active("HashMap"):
            self.generic_structs.by_name["HashMap"] = hashmap_generic_struct()
            self.generic_structs.order.append("HashMap")

        # List<T>: `{i32 len, i32 capacity, T* data}`, 2x growth, lazily allocated.
        # See docs/stdlib/collections/list.md.
        list_generic = GenericStructType(
            name="List",
            type_params=(TypeParameter(name="T"),),
            fields=(
                ("len", BuiltinType.I32),
                ("capacity", BuiltinType.I32),
                ("data", PointerType(BuiltinType.I32)),  # Placeholder for T*
            )
        )
        self.generic_structs.by_name["List"] = list_generic
        self.generic_structs.order.append("List")

        # Note: Generic enums and structs are not added to known_types until they are instantiated with concrete types
