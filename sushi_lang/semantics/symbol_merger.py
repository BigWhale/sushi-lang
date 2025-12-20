# semantics/symbol_merger.py
"""Symbol table merger for multi-file compilation.

Handles merging of symbol tables from multiple compilation units into global tables,
respecting visibility rules.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit
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
    )


class SymbolTableMerger:
    """Handles merging of symbol tables from multiple compilation units.

    Each merge method handles one symbol type, respecting visibility rules
    and detecting conflicts. All symbols are currently global (visible across units).
    """

    def merge_all(
        self,
        unit: 'Unit',
        unit_constants: 'ConstantTable',
        unit_structs: 'StructTable',
        unit_enums: 'EnumTable',
        unit_generic_enums: 'GenericEnumTable',
        unit_generic_structs: 'GenericStructTable',
        unit_perks: 'PerkTable',
        unit_perk_impls: 'PerkImplementationTable',
        unit_funcs: 'FunctionTable',
        unit_extensions: 'ExtensionTable',
        unit_generic_extensions: 'GenericExtensionTable',
        unit_generic_funcs: 'GenericFunctionTable',
        global_constants: 'ConstantTable',
        global_structs: 'StructTable',
        global_enums: 'EnumTable',
        global_generic_enums: 'GenericEnumTable',
        global_generic_structs: 'GenericStructTable',
        global_perks: 'PerkTable',
        global_perk_impls: 'PerkImplementationTable',
        global_funcs: 'FunctionTable',
        global_extensions: 'ExtensionTable',
        global_generic_extensions: 'GenericExtensionTable',
        global_generic_funcs: 'GenericFunctionTable',
    ) -> None:
        """Merge all symbols from a unit into global tables."""
        self._merge_constants(unit_constants, global_constants)
        self._merge_structs(unit_structs, global_structs)
        self._merge_enums(unit_enums, global_enums)
        self._merge_generic_enums(unit_generic_enums, global_generic_enums)
        self._merge_generic_structs(unit_generic_structs, global_generic_structs)
        self._merge_perks(unit_perks, global_perks)
        self._merge_perk_impls(unit_perk_impls, global_perk_impls)
        self._merge_functions(unit_funcs, global_funcs)
        self._merge_extensions(unit_extensions, global_extensions)
        self._merge_generic_extensions(unit_generic_extensions, global_generic_extensions)
        self._merge_generic_funcs(unit_generic_funcs, global_generic_funcs)

    def _merge_constants(
        self,
        unit_constants: 'ConstantTable',
        global_constants: 'ConstantTable',
    ) -> None:
        """Merge constants (all constants are global by design)."""
        for name, const_sig in unit_constants.by_name.items():
            if name not in global_constants.by_name:
                global_constants.by_name[name] = const_sig
                global_constants.order.append(name)

    def _merge_structs(
        self,
        unit_structs: 'StructTable',
        global_structs: 'StructTable',
    ) -> None:
        """Merge structs (all structs are global - visible across units)."""
        for name, struct_type in unit_structs.by_name.items():
            if name not in global_structs.by_name:
                global_structs.by_name[name] = struct_type
                global_structs.order.append(name)

    def _merge_enums(
        self,
        unit_enums: 'EnumTable',
        global_enums: 'EnumTable',
    ) -> None:
        """Merge enums (all enums are global - visible across units)."""
        for name, enum_type in unit_enums.by_name.items():
            if name not in global_enums.by_name:
                global_enums.by_name[name] = enum_type
                global_enums.order.append(name)

    def _merge_generic_enums(
        self,
        unit_generic_enums: 'GenericEnumTable',
        global_generic_enums: 'GenericEnumTable',
    ) -> None:
        """Merge generic enums (all generic enums are global - visible across units)."""
        for name, generic_enum in unit_generic_enums.by_name.items():
            if name not in global_generic_enums.by_name:
                global_generic_enums.by_name[name] = generic_enum
                global_generic_enums.order.append(name)

    def _merge_generic_structs(
        self,
        unit_generic_structs: 'GenericStructTable',
        global_generic_structs: 'GenericStructTable',
    ) -> None:
        """Merge generic structs (all generic structs are global - visible across units)."""
        for name, generic_struct in unit_generic_structs.by_name.items():
            if name not in global_generic_structs.by_name:
                global_generic_structs.by_name[name] = generic_struct
                global_generic_structs.order.append(name)

    def _merge_perks(
        self,
        unit_perks: 'PerkTable',
        global_perks: 'PerkTable',
    ) -> None:
        """Merge perks (all perks are global - visible across units)."""
        for name, perk_def in unit_perks.by_name.items():
            if name not in global_perks.by_name:
                global_perks.by_name[name] = perk_def
                global_perks.order.append(name)

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
        for name, func_sig in unit_funcs.by_name.items():
            if name not in global_funcs.by_name:
                global_funcs.by_name[name] = func_sig
                global_funcs.order.append(name)

        # Merge stdlib functions (registered from use <module> statements)
        for key, stdlib_func in unit_funcs._stdlib_functions.items():
            if key not in global_funcs._stdlib_functions:
                global_funcs._stdlib_functions[key] = stdlib_func

    def _merge_extensions(
        self,
        unit_extensions: 'ExtensionTable',
        global_extensions: 'ExtensionTable',
    ) -> None:
        """Merge extension methods (all extension methods are global)."""
        for target_type, methods in unit_extensions.by_type.items():
            if target_type not in global_extensions.by_type:
                global_extensions.by_type[target_type] = {}
            for method_name, method in methods.items():
                if method_name not in global_extensions.by_type[target_type]:
                    global_extensions.by_type[target_type][method_name] = method

    def _merge_generic_extensions(
        self,
        unit_generic_extensions: 'GenericExtensionTable',
        global_generic_extensions: 'GenericExtensionTable',
    ) -> None:
        """Merge generic extension methods (all generic extension methods are global)."""
        for base_type_name, methods in unit_generic_extensions.by_type.items():
            if base_type_name not in global_generic_extensions.by_type:
                global_generic_extensions.by_type[base_type_name] = {}
            for method_name, method in methods.items():
                if method_name not in global_generic_extensions.by_type[base_type_name]:
                    global_generic_extensions.by_type[base_type_name][method_name] = method

    def _merge_generic_funcs(
        self,
        unit_generic_funcs: 'GenericFunctionTable',
        global_generic_funcs: 'GenericFunctionTable',
    ) -> None:
        """Merge generic functions (all generic functions are global)."""
        for name, generic_func in unit_generic_funcs.by_name.items():
            if name not in global_generic_funcs.by_name:
                global_generic_funcs.by_name[name] = generic_func
                global_generic_funcs.order.append(name)
