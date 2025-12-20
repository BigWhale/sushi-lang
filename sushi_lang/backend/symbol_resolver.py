"""Symbol resolution and deduplication for two-phase linking.

This module resolves symbol conflicts when multiple modules define the same symbol.
It applies priority rules: main program > user libraries > stdlib > runtime.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.backend.symbol_table import SymbolInfo, SymbolTable


class SymbolResolver:
    """Resolves symbol conflicts and selects which definitions to use."""

    def __init__(self, symbol_tables: list['SymbolTable'], verbose: bool = False):
        """Initialize resolver with all symbol tables.

        Args:
            symbol_tables: List of tables from main, libraries, stdlib, runtime.
            verbose: If True, print conflict resolution messages.
        """
        self.symbol_tables = symbol_tables
        self.verbose = verbose
        self.resolution_map: dict[str, 'SymbolInfo'] = {}  # Final symbol choices
        self.conflicts: list[tuple[str, list['SymbolInfo']]] = []  # Track conflicts

    def resolve(self, reachable_symbols: set[str]) -> dict[str, 'SymbolInfo']:
        """Resolve all reachable symbols, handling duplicates.

        Args:
            reachable_symbols: Set of symbol names that are actually used.

        Returns:
            Mapping of symbol_name -> chosen SymbolInfo.
        """
        # Build index of all available definitions per symbol
        definitions: dict[str, list['SymbolInfo']] = {}
        declarations: dict[str, list['SymbolInfo']] = {}

        for table in self.symbol_tables:
            for name, symbol in table.symbols.items():
                if name not in reachable_symbols:
                    continue  # Skip unreachable symbols

                if symbol.is_definition():
                    if name not in definitions:
                        definitions[name] = []
                    definitions[name].append(symbol)
                else:
                    if name not in declarations:
                        declarations[name] = []
                    declarations[name].append(symbol)

        # Resolve each symbol using priority rules
        for symbol_name in reachable_symbols:
            defs = definitions.get(symbol_name, [])

            if len(defs) == 0:
                # No definition found - must be external (libc, etc.)
                # Keep as declaration from any table that has it
                decls = declarations.get(symbol_name, [])
                if decls:
                    self.resolution_map[symbol_name] = decls[0]
                # If no declaration either, it's truly external - skip
            elif len(defs) == 1:
                # Unique definition - use it
                self.resolution_map[symbol_name] = defs[0]
            else:
                # Multiple definitions - apply priority rules
                chosen = self._choose_definition(symbol_name, defs)
                self.resolution_map[symbol_name] = chosen
                self.conflicts.append((symbol_name, defs))

        return self.resolution_map

    def _choose_definition(
        self,
        symbol_name: str,
        candidates: list['SymbolInfo']
    ) -> 'SymbolInfo':
        """Choose which definition to use when multiple exist.

        Priority order:
        1. MAIN (main program) - highest priority
        2. LIBRARY (user libraries)
        3. STDLIB (standard library)
        4. RUNTIME (runtime functions)

        If same priority, choose first occurrence (deterministic).

        Args:
            symbol_name: Name of the conflicting symbol.
            candidates: List of competing definitions.

        Returns:
            The chosen SymbolInfo.
        """
        # Sort by priority (lower enum value = higher priority)
        candidates_sorted = sorted(candidates, key=lambda s: s.source.value)

        chosen = candidates_sorted[0]

        # Log the choice for debugging
        if self.verbose and len(candidates) > 1:
            others = [f"{s.module_name}({s.source.name})" for s in candidates_sorted[1:]]
            print(f"  Symbol conflict '{symbol_name}': "
                  f"chose {chosen.module_name}({chosen.source.name}) "
                  f"over {', '.join(others)}")

        return chosen

    def get_conflicts(self) -> list[tuple[str, list['SymbolInfo']]]:
        """Get list of all symbol conflicts that were resolved.

        Returns:
            List of (symbol_name, list_of_competing_definitions) tuples.
        """
        return self.conflicts

    def get_conflict_summary(self) -> str:
        """Get a human-readable summary of resolved conflicts.

        Returns:
            Multi-line string summarizing all conflicts.
        """
        if not self.conflicts:
            return "No symbol conflicts detected."

        lines = [f"Resolved {len(self.conflicts)} symbol conflict(s):"]
        for symbol_name, defs in self.conflicts:
            chosen = self.resolution_map[symbol_name]
            sources = [f"{d.module_name}({d.source.name})" for d in defs]
            lines.append(f"  {symbol_name}: {' vs '.join(sources)} -> {chosen.module_name}")

        return '\n'.join(lines)
