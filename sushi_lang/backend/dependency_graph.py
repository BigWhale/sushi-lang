"""Dependency graph builder for symbol resolution."""
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
        """Record that from_symbol references to_symbol."""
        if from_symbol not in self.edges:
            self.edges[from_symbol] = set()
        self.edges[from_symbol].add(to_symbol)

    def get_dependencies(self, symbol: str) -> set[str]:
        """Get all symbols directly referenced by this symbol."""
        return self.edges.get(symbol, set())

    def get_transitive_closure(self, root_symbols: set[str]) -> set[str]:
        """Compute transitive closure of dependencies starting from root symbols."""
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
    """Extract all symbol references from LLVM IR text."""
    matches = _SYMBOL_REFERENCE_RE.findall(ir_text)

    references = set()
    for match in matches:
        if match.startswith('"') and match.endswith('"'):
            references.add(match[1:-1])  # Strip quotes
        else:
            if not match.startswith("llvm."):
                references.add(match)

    return references


def build_dependency_graph(symbol_tables: list['SymbolTable']) -> DependencyGraph:
    """Build dependency graph from symbol tables."""
    graph = DependencyGraph()

    all_symbols: dict[str, 'SymbolInfo'] = {}
    for table in symbol_tables:
        for name, symbol in table.symbols.items():
            if name not in all_symbols:
                all_symbols[name] = symbol

    definitions: dict[str, 'SymbolInfo'] = {}
    for table in symbol_tables:
        for name, symbol in table.symbols.items():
            if symbol.is_definition():
                if name not in definitions:
                    definitions[name] = symbol

    for symbol in definitions.values():
        if symbol.ir_text is None:
            continue  # Should not happen for definitions, but be safe

        referenced = extract_symbol_references(symbol.ir_text)

        for ref in referenced:
            if ref in all_symbols or ref == symbol.name:
                graph.add_dependency(symbol.name, ref)

    return graph
