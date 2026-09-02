"""Aggregate container for the whole-program symbol tables."""
from __future__ import annotations

from dataclasses import dataclass, field

from sushi_lang.semantics.visibility import VisibilityTable
from sushi_lang.semantics.passes.collect import (
    ConstantTable,
    StructTable,
    EnumTable,
    GenericEnumTable,
    GenericStructTable,
    PerkTable,
    PerkImplementationTable,
    GenericPerkImplTable,
    FunctionTable,
    ExtensionTable,
    GenericExtensionTable,
    GenericFunctionTable,
    ExternalTable,
)


@dataclass
class SymbolTables:
    """Whole-program symbol tables collected by the collect pass."""

    constants: ConstantTable = field(default_factory=ConstantTable)
    structs: StructTable = field(default_factory=StructTable)
    enums: EnumTable = field(default_factory=EnumTable)
    generic_enums: GenericEnumTable = field(default_factory=GenericEnumTable)
    generic_structs: GenericStructTable = field(default_factory=GenericStructTable)
    perks: PerkTable = field(default_factory=PerkTable)
    perk_impls: PerkImplementationTable = field(default_factory=PerkImplementationTable)
    # The perk implementations whose target names a type PARAMETER. Templates: one copy
    # per instantiation joins `perk_impls` after monomorphization.
    generic_perk_impls: GenericPerkImplTable = field(default_factory=GenericPerkImplTable)
    funcs: FunctionTable = field(default_factory=FunctionTable)
    extensions: ExtensionTable = field(default_factory=ExtensionTable)
    generic_extensions: GenericExtensionTable = field(default_factory=GenericExtensionTable)
    generic_funcs: GenericFunctionTable = field(default_factory=GenericFunctionTable)
    externals: ExternalTable = field(default_factory=ExternalTable)
    # Who declared what, and whether it says `public`. The kinds whose own table carries
    # no unit and no marker -- a struct, an enum, a perk -- are answered from here.
    visibility: VisibilityTable = field(default_factory=VisibilityTable)
    # A name a linked library declares and keeps: name -> (library, kind) (#469). Not a
    # table of callables -- no signature travels with a kept name, so it holds what the
    # CE3005 gate needs and nothing more.
    library_not_exported: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Extension instantiations the typecheck pass requested at CALL SITES:
    # (template, target_type, receiver_args, method_type_args). An array template's
    # element and a method-generic's solved margs both live only at the call, so these
    # queue during the per-unit loop and the analyzer monomorphizes and checks them to
    # a fixpoint after. `queued_extension_keys` dedupes: one copy per
    # (receiver, method, margs), which is also what keeps the weak_odr symbol unique.
    pending_extension_instantiations: list = field(default_factory=list)
    queued_extension_keys: set = field(default_factory=set)
    # The analyzer's late interner (risk 1): the typecheck pass hands it a type whose
    # generic instantiations may not be interned yet. None outside a full analysis.
    intern_generic_ref: object = None
