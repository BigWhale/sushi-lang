"""Two-phase linking with symbol deduplication.

Despite where it used to live, this is NOT library-specific: the main module and the
stdlib bitcode go through it too. It sits beside its four collaborators --
symbol_table, dependency_graph, symbol_resolver, module_merger.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import llvmlite.binding as llvm

if TYPE_CHECKING:
    from sushi_lang.backend.symbol_table import SymbolSource


class TwoPhaseLinker:
    """Implements two-phase linking with symbol deduplication.

    Two-phase linking resolves the "symbol multiply defined" error that occurs
    when both the main program and libraries define the same symbols (e.g.,
    runtime functions like utf8_char_count).

    Phase 1: Extract symbol tables from all modules
    Phase 2: Build dependency graph from entry points
    Phase 3: Resolve symbol conflicts using priority rules
    Phase 4: Merge resolved symbols into final module
    """

    def __init__(self, target_triple: str = "", data_layout: str = ""):
        """Initialize two-phase linker.

        Args:
            target_triple: LLVM target triple for the platform.
            data_layout: LLVM data layout string.
        """
        self.target_triple = target_triple
        self.data_layout = data_layout
        self.modules: list[tuple[llvm.ModuleRef, str, 'SymbolSource']] = []

    def add_main_module(self, module: llvm.ModuleRef, name: str = "main") -> None:
        """Add the main program module.

        Args:
            module: Parsed main program bitcode.
            name: Module name for debugging.
        """
        from sushi_lang.backend.symbol_table import SymbolSource
        self.modules.append((module, name, SymbolSource.MAIN))

    def add_library_module(self, module: llvm.ModuleRef, name: str) -> None:
        """Add a user library module.

        Args:
            module: Parsed library bitcode.
            name: Library name.
        """
        from sushi_lang.backend.symbol_table import SymbolSource
        self.modules.append((module, name, SymbolSource.LIBRARY))

    def add_stdlib_module(self, module: llvm.ModuleRef, name: str) -> None:
        """Add a standard library module.

        Args:
            module: Parsed stdlib bitcode.
            name: Stdlib unit name (e.g., "io/stdio").
        """
        from sushi_lang.backend.symbol_table import SymbolSource
        self.modules.append((module, name, SymbolSource.STDLIB))

    def _find_global_constructors(self) -> set[str]:
        """Find all global constructor functions in the modules.

        LLVM uses @llvm.global_ctors to register initialization functions that
        must run before main(). These are entry points that must be included
        in the reachable set.

        Returns:
            Set of constructor function names.
        """
        import re
        constructors = set()

        # Pattern to match constructor entries in @llvm.global_ctors
        # Format: { i32 priority, void ()* @func_name, i8* null }
        ctor_pattern = re.compile(r'@llvm\.global_ctors.*?@([a-zA-Z_][a-zA-Z0-9_\.]*)')

        for module, _name, _source in self.modules:
            # Get full IR text and search for global_ctors
            ir_text = str(module)
            for match in ctor_pattern.finditer(ir_text):
                constructors.add(match.group(1))

        return constructors

    def link(self, entry_points: list[str] | None = None) -> llvm.ModuleRef:
        """Perform two-phase linking and return merged module.

        Args:
            entry_points: List of entry point symbols (default: ["main"]).

        Returns:
            New LLVM module with deduplicated symbols.
        """
        from sushi_lang.backend.symbol_table import extract_symbol_table
        from sushi_lang.backend.dependency_graph import build_dependency_graph
        from sushi_lang.backend.symbol_resolver import SymbolResolver
        from sushi_lang.backend.module_merger import ModuleMerger

        if entry_points is None:
            entry_points = ["main"]

        # Also include global constructors as entry points
        constructors = self._find_global_constructors()
        if constructors:
            entry_points = list(set(entry_points) | constructors)

        # Phase 1: Extract symbol tables from all modules
        symbol_tables = []
        for module, name, source in self.modules:
            symbol_tables.append(extract_symbol_table(module, name, source))

        # Phase 2: Build dependency graph and compute reachable symbols
        graph = build_dependency_graph(symbol_tables)
        reachable = graph.get_transitive_closure(set(entry_points))

        # Phase 3: Resolve duplicates
        resolver = SymbolResolver(symbol_tables)
        resolved = resolver.resolve(reachable)

        # Phase 4: Merge into new module
        merger = ModuleMerger(self.target_triple, self.data_layout)
        return merger.merge(resolved, "sushi_linked")
