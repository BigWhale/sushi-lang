# semantics/tables.py
"""Aggregate container for the whole-program symbol tables.

The collector, merger, type validator, and backend all pass the same set of
symbol tables around. Before this, they were threaded as an 11-tuple return and
15-to-23 positional parameters. ``SymbolTables`` bundles them into one object so
adding a table is a one-line change instead of a multi-file signature sweep.

``externals`` accumulates in the shared collector rather than per unit, so it is
threaded here for completeness but merged separately from the other eleven.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sushi_lang.semantics.passes.collect import (
    ConstantTable,
    StructTable,
    EnumTable,
    GenericEnumTable,
    GenericStructTable,
    PerkTable,
    PerkImplementationTable,
    FunctionTable,
    ExtensionTable,
    GenericExtensionTable,
    GenericFunctionTable,
    ExternalTable,
)


@dataclass
class SymbolTables:
    """Whole-program symbol tables collected in Pass 0."""

    constants: ConstantTable = field(default_factory=ConstantTable)
    structs: StructTable = field(default_factory=StructTable)
    enums: EnumTable = field(default_factory=EnumTable)
    generic_enums: GenericEnumTable = field(default_factory=GenericEnumTable)
    generic_structs: GenericStructTable = field(default_factory=GenericStructTable)
    perks: PerkTable = field(default_factory=PerkTable)
    perk_impls: PerkImplementationTable = field(default_factory=PerkImplementationTable)
    funcs: FunctionTable = field(default_factory=FunctionTable)
    extensions: ExtensionTable = field(default_factory=ExtensionTable)
    generic_extensions: GenericExtensionTable = field(default_factory=GenericExtensionTable)
    generic_funcs: GenericFunctionTable = field(default_factory=GenericFunctionTable)
    externals: ExternalTable = field(default_factory=ExternalTable)
