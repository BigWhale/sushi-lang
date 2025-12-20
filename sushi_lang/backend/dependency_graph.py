"""Dependency graph builder for symbol resolution.

This module builds a dependency graph showing which symbols reference which other
symbols. It's the second phase of the two-phase linking process, enabling dead
code elimination by finding all symbols reachable from entry points.
"""
from __future__ import annotations
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.backend.symbol_table import SymbolInfo, SymbolTable


# Compiled regex for extracting symbol references from LLVM IR
# Matches @symbol_name, @.symbol_name, or @"quoted name" patterns
# Note: LLVM uses names like .fmt.i32 for format strings (starting with dot)
_SYMBOL_REFERENCE_RE = re.compile(r'@(\.?[a-zA-Z_][a-zA-Z0-9_\.]*|"[^"]+")')


class DependencyGraph:
    """Tracks which symbols depend on which other symbols."""

    def __init__(self):
        """Initialize empty dependency graph."""
        self.edges: dict[str, set[str]] = {}  # symbol_name -> set of referenced symbols

    def add_dependency(self, from_symbol: str, to_symbol: str) -> None:
        """Record that from_symbol references to_symbol.

        Args:
            from_symbol: The symbol that has a reference.
            to_symbol: The symbol being referenced.
        """
        if from_symbol not in self.edges:
            self.edges[from_symbol] = set()
        self.edges[from_symbol].add(to_symbol)

    def get_dependencies(self, symbol: str) -> set[str]:
        """Get all symbols directly referenced by this symbol.

        Args:
            symbol: Symbol name to look up.

        Returns:
            Set of symbol names referenced by this symbol.
        """
        return self.edges.get(symbol, set())

    def get_transitive_closure(self, root_symbols: set[str]) -> set[str]:
        """Compute transitive closure of dependencies starting from root symbols.

        This implements a breadth-first search to find all reachable symbols.

        Args:
            root_symbols: Set of entry point symbols (e.g., {"main"}).

        Returns:
            Set of all symbols reachable from root_symbols.
        """
        reachable = set(root_symbols)
        worklist = list(root_symbols)

        while worklist:
            current = worklist.pop(0)
            deps = self.get_dependencies(current)

            for dep in deps:
                if dep not in reachable:
                    reachable.add(dep)
                    worklist.append(dep)

        return reachable

    def __repr__(self) -> str:
        total_edges = sum(len(deps) for deps in self.edges.values())
        return f"DependencyGraph({len(self.edges)} symbols, {total_edges} edges)"


def extract_symbol_references(ir_text: str) -> set[str]:
    """Extract all symbol references from LLVM IR text.

    Looks for patterns like:
    - @function_name (function calls, declarations)
    - @global_var (global variable references)
    - @"quoted.name" (mangled or special names)

    Args:
        ir_text: LLVM IR code as string.

    Returns:
        Set of referenced symbol names (without @ prefix).
    """
    matches = _SYMBOL_REFERENCE_RE.findall(ir_text)

    references = set()
    for match in matches:
        # Clean up quoted names (remove quotes)
        if match.startswith('"') and match.endswith('"'):
            references.add(match[1:-1])  # Strip quotes
        else:
            # Skip LLVM intrinsics - they're not user symbols
            if not match.startswith("llvm."):
                references.add(match)

    return references


def build_dependency_graph(symbol_tables: list['SymbolTable']) -> DependencyGraph:
    """Build dependency graph from symbol tables.

    Parses IR text of each function/global to find references to other symbols.

    Args:
        symbol_tables: List of symbol tables from all modules.

    Returns:
        Complete dependency graph.
    """
    graph = DependencyGraph()

    # Create unified symbol lookup across all tables
    # We need to track all symbols to know what's a valid reference
    all_symbols: dict[str, 'SymbolInfo'] = {}
    for table in symbol_tables:
        for name, symbol in table.symbols.items():
            # First occurrence wins (main program symbols take precedence)
            if name not in all_symbols:
                all_symbols[name] = symbol

    # For dependency analysis, we need the DEFINITION of each symbol
    # (not just declarations), so create a separate lookup for definitions
    definitions: dict[str, 'SymbolInfo'] = {}
    for table in symbol_tables:
        for name, symbol in table.symbols.items():
            if symbol.is_definition():
                # Prefer first definition found (main > library > stdlib)
                if name not in definitions:
                    definitions[name] = symbol

    # Parse each definition's IR to find references
    for symbol in definitions.values():
        if symbol.ir_text is None:
            continue  # Should not happen for definitions, but be safe

        # Extract symbol references from IR text
        referenced = extract_symbol_references(symbol.ir_text)

        for ref in referenced:
            # Only add edge if the reference is to a known symbol
            # This filters out external symbols like libc functions
            if ref in all_symbols or ref == symbol.name:
                graph.add_dependency(symbol.name, ref)

    return graph
