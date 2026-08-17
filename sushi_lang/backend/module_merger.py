"""Module merger for two-phase linking."""
from __future__ import annotations
import re
from typing import TYPE_CHECKING

import llvmlite.binding as llvm

if TYPE_CHECKING:
    from sushi_lang.backend.symbol_table import SymbolInfo


class ModuleMerger:
    """Builds a new LLVM module from resolved symbols."""

    def __init__(self, target_triple: str = "", data_layout: str = ""):
        """Initialize merger."""
        self.target_triple = target_triple
        self.data_layout = data_layout

    def merge(
        self,
        resolved_symbols: dict[str, 'SymbolInfo'],
        module_name: str = "merged",
        module_type_defs: set[str] | None = None
    ) -> llvm.ModuleRef:
        """Build new module from resolved symbols."""
        type_defs = self._extract_type_definitions(resolved_symbols)
        if module_type_defs:
            type_defs |= module_type_defs

        ir_parts = [
            f'; ModuleID = "{module_name}"',
            f'source_filename = "{module_name}"',
        ]

        if self.target_triple:
            ir_parts.append(f'target triple = "{self.target_triple}"')

        if self.data_layout:
            ir_parts.append(f'target datalayout = "{self.data_layout}"')

        ir_parts.append('')  # Blank line

        if type_defs:
            ir_parts.extend(sorted(type_defs))
            ir_parts.append('')

        declarations = []
        definitions = []

        for _symbol_name, symbol in resolved_symbols.items():
            if symbol.ir_text is None:
                continue

            ir_text = self._strip_type_definitions(symbol.ir_text)

            if symbol.is_declaration:
                declarations.append(ir_text)
            else:
                definitions.append(ir_text)

        ir_parts.extend(declarations)
        if declarations and definitions:
            ir_parts.append('')
        ir_parts.extend(definitions)

        full_ir = '\n'.join(ir_parts)

        try:
            merged_module = llvm.parse_assembly(full_ir)
            return merged_module
        except Exception as e:
            debug_path = '/tmp/sushi_merge_failed.ll'
            with open(debug_path, 'w') as f:
                f.write(full_ir)
            raise RuntimeError(
                f"Failed to parse merged IR. Debug IR written to {debug_path}\n"
                f"Error: {e}"
            ) from e

    def _extract_type_definitions(
        self,
        resolved_symbols: dict[str, 'SymbolInfo']
    ) -> set[str]:
        """Extract all type definitions from symbol IR texts."""
        type_defs = set()

        type_def_pattern = re.compile(
            r'^(%[a-zA-Z_][a-zA-Z0-9_\.]*|%"[^"]+") = type \{[^}]*\}',
            re.MULTILINE
        )

        for symbol in resolved_symbols.values():
            if symbol.ir_text is None:
                continue

            matches = type_def_pattern.findall(symbol.ir_text)
            for match in matches:
                for line in symbol.ir_text.split('\n'):
                    if line.strip().startswith(match) and '= type' in line:
                        type_defs.add(line.strip())
                        break

        return type_defs

    def _strip_type_definitions(self, ir_text: str) -> str:
        """Remove type definition lines from IR text."""
        lines = ir_text.split('\n')
        filtered = []

        for line in lines:
            stripped = line.strip()
            if '= type {' in stripped and stripped.startswith('%'):
                continue
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
