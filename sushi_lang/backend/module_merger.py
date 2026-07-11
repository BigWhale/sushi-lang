"""Module merger for two-phase linking.

This module builds a new LLVM module containing only the resolved symbols.
It reconstructs valid LLVM IR from the symbol definitions collected during
the resolution phase.
"""
from __future__ import annotations
import re
from typing import TYPE_CHECKING

import llvmlite.binding as llvm

if TYPE_CHECKING:
    from sushi_lang.backend.symbol_table import SymbolInfo, SymbolType


class ModuleMerger:
    """Builds a new LLVM module from resolved symbols."""

    def __init__(self, target_triple: str = "", data_layout: str = ""):
        """Initialize merger.

        Args:
            target_triple: LLVM target triple (e.g., "x86_64-apple-darwin").
            data_layout: LLVM data layout string.
        """
        self.target_triple = target_triple
        self.data_layout = data_layout

    def merge(
        self,
        resolved_symbols: dict[str, 'SymbolInfo'],
        module_name: str = "merged"
    ) -> llvm.ModuleRef:
        """Build new module from resolved symbols.

        Strategy: Concatenate IR text of all chosen symbols and parse as one module.

        Args:
            resolved_symbols: Map of symbol_name -> chosen SymbolInfo.
            module_name: Name for the new module.

        Returns:
            New LLVM module with deduplicated symbols.

        Raises:
            RuntimeError: If the merged IR fails to parse.
        """
        # Collect all type definitions from all symbol IR texts
        # We need to do this because type definitions must come before uses
        type_defs = self._extract_type_definitions(resolved_symbols)

        # Build IR text by concatenating all symbol definitions
        ir_parts = [
            f'; ModuleID = "{module_name}"',
            f'source_filename = "{module_name}"',
        ]

        if self.target_triple:
            ir_parts.append(f'target triple = "{self.target_triple}"')

        if self.data_layout:
            ir_parts.append(f'target datalayout = "{self.data_layout}"')

        ir_parts.append('')  # Blank line

        # Add collected type definitions
        if type_defs:
            ir_parts.extend(sorted(type_defs))
            ir_parts.append('')

        # Separate declarations from definitions for cleaner IR
        declarations = []
        definitions = []

        for symbol_name, symbol in resolved_symbols.items():
            if symbol.ir_text is None:
                continue

            # Strip type definitions from individual IR texts
            # (we already collected them above)
            ir_text = self._strip_type_definitions(symbol.ir_text)

            if symbol.is_declaration:
                declarations.append(ir_text)
            else:
                definitions.append(ir_text)

        # Declarations first, then definitions
        ir_parts.extend(declarations)
        if declarations and definitions:
            ir_parts.append('')
        ir_parts.extend(definitions)

        # Join and parse
        full_ir = '\n'.join(ir_parts)

        try:
            merged_module = llvm.parse_assembly(full_ir)
            return merged_module
        except Exception as e:
            # Debug: write IR to file for inspection
            debug_path = '/tmp/sushi_merge_failed.ll'
            with open(debug_path, 'w') as f:
                f.write(full_ir)
            raise RuntimeError(
                f"Failed to parse merged IR. Debug IR written to {debug_path}\n"
                f"Error: {e}"
            )

    def _extract_type_definitions(
        self,
        resolved_symbols: dict[str, 'SymbolInfo']
    ) -> set[str]:
        """Extract all type definitions from symbol IR texts.

        LLVM IR type definitions look like:
            %struct.Point = type { i32, i32 }
            %"HashMap<i32, string>" = type { ... }

        Args:
            resolved_symbols: Map of symbol_name -> SymbolInfo.

        Returns:
            Set of unique type definition lines.
        """
        type_defs = set()

        # Regex to match type definitions
        # Matches: %name = type { ... } or %"name" = type { ... }
        type_def_pattern = re.compile(
            r'^(%[a-zA-Z_][a-zA-Z0-9_\.]*|%"[^"]+") = type \{[^}]*\}',
            re.MULTILINE
        )

        for symbol in resolved_symbols.values():
            if symbol.ir_text is None:
                continue

            matches = type_def_pattern.findall(symbol.ir_text)
            for match in matches:
                # Get the full line containing this type definition
                for line in symbol.ir_text.split('\n'):
                    if line.strip().startswith(match) and '= type' in line:
                        type_defs.add(line.strip())
                        break

        return type_defs

    def _strip_type_definitions(self, ir_text: str) -> str:
        """Remove type definition lines from IR text.

        We extract type definitions separately to avoid duplicates.

        Args:
            ir_text: Original IR text.

        Returns:
            IR text with type definition lines removed.
        """
        lines = ir_text.split('\n')
        filtered = []

        for line in lines:
            stripped = line.strip()
            # Skip type definition lines
            if '= type {' in stripped and stripped.startswith('%'):
                continue
            # Skip module-level metadata we'll add ourselves
            if stripped.startswith('; ModuleID'):
                continue
            if stripped.startswith('source_filename'):
                continue
            if stripped.startswith('target triple'):
                continue
            if stripped.startswith('target datalayout'):
                continue
            filtered.append(line)

        return '\n'.join(filtered)
