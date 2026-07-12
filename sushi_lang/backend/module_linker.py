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

    def __init__(self, target_triple: str = "", data_layout: str = "", verbose: bool = False):
        """Initialize two-phase linker.

        Args:
            target_triple: LLVM target triple for the platform.
            data_layout: LLVM data layout string.
            verbose: If True, print detailed linking information.
        """
        self.target_triple = target_triple
        self.data_layout = data_layout
        self.verbose = verbose
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

        for module, name, source in self.modules:
            # Get full IR text and search for global_ctors
            ir_text = str(module)
            for match in ctor_pattern.finditer(ir_text):
                ctor_name = match.group(1)
                constructors.add(ctor_name)
                if self.verbose:
                    print(f"  Found global constructor: {ctor_name} in {name}")

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
            if self.verbose:
                print(f"Two-phase linking: Added {len(constructors)} global constructor(s) to entry points")

        if self.verbose:
            print("Two-phase linking: Extracting symbol tables...")

        # Phase 1: Extract symbol tables from all modules
        symbol_tables = []
        for module, name, source in self.modules:
            table = extract_symbol_table(module, name, source)
            symbol_tables.append(table)
            if self.verbose:
                print(f"  {table}")

        # Phase 2: Build dependency graph
        if self.verbose:
            print("Two-phase linking: Building dependency graph...")
        graph = build_dependency_graph(symbol_tables)
        if self.verbose:
            print(f"  {graph}")

        # Compute reachable symbols from entry points
        if self.verbose:
            print(f"Two-phase linking: Finding reachable symbols from {entry_points}...")
        reachable = graph.get_transitive_closure(set(entry_points))
        if self.verbose:
            print(f"  Found {len(reachable)} reachable symbols")

        # Phase 3: Resolve duplicates
        if self.verbose:
            print("Two-phase linking: Resolving symbol conflicts...")
        resolver = SymbolResolver(symbol_tables, verbose=self.verbose)
        resolved = resolver.resolve(reachable)

        if self.verbose:
            conflicts = resolver.get_conflicts()
            if conflicts:
                print(f"  Resolved {len(conflicts)} symbol conflict(s)")

        # Phase 4: Merge into new module
        if self.verbose:
            print("Two-phase linking: Merging symbols into final module...")
        merger = ModuleMerger(self.target_triple, self.data_layout)
        merged_module = merger.merge(resolved, "sushi_linked")

        if self.verbose:
            func_count = len(list(merged_module.functions))
            global_count = len(list(merged_module.global_variables))
            print(f"Two-phase linking: Complete. "
                  f"Final module has {func_count} functions, {global_count} globals")

        return merged_module
