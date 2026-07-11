# semantics/symbol_merger.py
"""Symbol table merger for multi-file compilation.

Handles merging of symbol tables from multiple compilation units into global tables,
respecting visibility rules.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.passes.collect import (
        PerkImplementationTable,
        FunctionTable,
    )


class SymbolTableMerger:
    """Handles merging of symbol tables from multiple compilation units.

    Each merge method handles one symbol type, respecting visibility rules
    and detecting conflicts. All symbols are currently global (visible across units).
    """

    def merge_all(
        self,
        unit: 'Unit',
        unit_tables: 'SymbolTables',
        global_tables: 'SymbolTables',
    ) -> None:
        """Merge all symbols from a unit into the global tables.

        Externals are not merged here: they accumulate in the shared collector
        table rather than per unit.
        """
        self._merge_by_name(unit_tables.constants, global_tables.constants)
        self._merge_by_name(unit_tables.structs, global_tables.structs)
        self._merge_by_name(unit_tables.enums, global_tables.enums)
        self._merge_by_name(unit_tables.generic_enums, global_tables.generic_enums)
        self._merge_by_name(unit_tables.generic_structs, global_tables.generic_structs)
        self._merge_by_name(unit_tables.perks, global_tables.perks)
        self._merge_perk_impls(unit_tables.perk_impls, global_tables.perk_impls)
        self._merge_functions(unit_tables.funcs, global_tables.funcs)
        self._merge_by_type(unit_tables.extensions, global_tables.extensions)
        self._merge_by_type(unit_tables.generic_extensions, global_tables.generic_extensions)
        self._merge_by_name(unit_tables.generic_funcs, global_tables.generic_funcs)

    @staticmethod
    def _merge_by_name(unit_table, global_table) -> None:
        """Merge a name-keyed table into the global one (all symbols global).

        Covers constants, structs, enums, perks, and their generic counterparts:
        first-writer-wins on a name, preserving insertion order.
        """
        for name, value in unit_table.by_name.items():
            if name not in global_table.by_name:
                global_table.by_name[name] = value
                global_table.order.append(name)

    @staticmethod
    def _merge_by_type(unit_table, global_table) -> None:
        """Merge a type-keyed table of per-method dicts (extension methods).

        First-writer-wins on each (target type, method name).
        """
        for type_name, methods in unit_table.by_type.items():
            target = global_table.by_type.setdefault(type_name, {})
            for method_name, method in methods.items():
                if method_name not in target:
                    target[method_name] = method

    def _merge_perk_impls(
        self,
        unit_perk_impls: 'PerkImplementationTable',
        global_perk_impls: 'PerkImplementationTable',
    ) -> None:
        """Merge perk implementations (all perk implementations are global)."""
        for key, impl in unit_perk_impls.implementations.items():
            if key not in global_perk_impls.implementations:
                type_name, perk_name = key
                global_perk_impls.implementations[key] = impl
                # Update indexes
                if type_name not in global_perk_impls.by_type:
                    global_perk_impls.by_type[type_name] = set()
                global_perk_impls.by_type[type_name].add(perk_name)
                if perk_name not in global_perk_impls.by_perk:
                    global_perk_impls.by_perk[perk_name] = set()
                global_perk_impls.by_perk[perk_name].add(type_name)

    def _merge_functions(
        self,
        unit_funcs: 'FunctionTable',
        global_funcs: 'FunctionTable',
    ) -> None:
        """Merge functions (both public and private are tracked).

        Note: Visibility checking is done during type validation, not during merge.
        All functions need to be in the global table so that we can check visibility.
        """
        self._merge_by_name(unit_funcs, global_funcs)

        # Merge stdlib functions (registered from use <module> statements)
        for key, stdlib_func in unit_funcs._stdlib_functions.items():
            if key not in global_funcs._stdlib_functions:
                global_funcs._stdlib_functions[key] = stdlib_func
