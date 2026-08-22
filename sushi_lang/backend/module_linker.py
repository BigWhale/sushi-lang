"""Two-phase linking with symbol deduplication."""
from __future__ import annotations

from typing import TYPE_CHECKING

import llvmlite.binding as llvm

if TYPE_CHECKING:
    from sushi_lang.backend.symbol_table import SymbolSource


class TwoPhaseLinker:
    """Implements two-phase linking with symbol deduplication."""

    def __init__(self, target_triple: str = "", data_layout: str = ""):
        """Initialize two-phase linker."""
        self.target_triple = target_triple
        self.data_layout = data_layout
        self.modules: list[tuple[llvm.ModuleRef, str, 'SymbolSource']] = []

    def add_main_module(self, module: llvm.ModuleRef, name: str = "main") -> None:
        """Add the main program module."""
        from sushi_lang.backend.symbol_table import SymbolSource
        self.modules.append((module, name, SymbolSource.MAIN))

    def add_library_module(self, module: llvm.ModuleRef, name: str) -> None:
        """Add a user library module."""
        from sushi_lang.backend.symbol_table import SymbolSource
        self.modules.append((module, name, SymbolSource.LIBRARY))

    def add_stdlib_module(self, module: llvm.ModuleRef, name: str) -> None:
        """Add a standard library module."""
        from sushi_lang.backend.symbol_table import SymbolSource
        self.modules.append((module, name, SymbolSource.STDLIB))

    def _find_global_constructors(self) -> set[str]:
        """Find all global constructor functions in the modules."""
        import re
        constructors = set()

        ctor_pattern = re.compile(r'@llvm\.global_ctors.*?@([a-zA-Z_][a-zA-Z0-9_\.]*)')

        for module, _name, _source in self.modules:
            ir_text = str(module)
            for match in ctor_pattern.finditer(ir_text):
                constructors.add(match.group(1))

        return constructors

    def link(self, entry_points: list[str] | None = None) -> llvm.ModuleRef:
        """Perform two-phase linking and return merged module."""
        from sushi_lang.backend.symbol_table import extract_symbol_table
        from sushi_lang.backend.dependency_graph import build_dependency_graph
        from sushi_lang.backend.symbol_resolver import SymbolResolver
        from sushi_lang.backend.module_merger import ModuleMerger

        if entry_points is None:
            entry_points = ["main"]

        constructors = self._find_global_constructors()
        if constructors:
            entry_points = list(set(entry_points) | constructors)

        symbol_tables = []
        for module, name, source in self.modules:
            symbol_tables.append(extract_symbol_table(module, name, source))

        graph = build_dependency_graph(symbol_tables)
        reachable = graph.get_transitive_closure(set(entry_points))

        resolver = SymbolResolver(symbol_tables)
        resolved = resolver.resolve(reachable)

        # Merge into the new module. The identified-type declarations travel
        # separately from the symbols because they are module-level state (#257).
        module_type_defs: set[str] = set()
        for table in symbol_tables:
            module_type_defs |= table.type_defs

        merger = ModuleMerger(self.target_triple, self.data_layout)
        return merger.merge(resolved, "sushi_linked", module_type_defs)
