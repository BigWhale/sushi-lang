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


def get_defined_symbols(module: llvm.ModuleRef) -> set[str]:
    """Get set of symbol names that are defined (not just declared) in a module.

    Args:
        module: LLVM module to inspect.

    Returns:
        Set of symbol names with definitions.
    """
    defined = set()
    for func in module.functions:
        if not func.is_declaration:
            defined.add(func.name)
    for gv in module.global_variables:
        if not gv.is_declaration:
            defined.add(gv.name)
    return defined


def strip_conflicting_definitions(
    lib_ir: str,
    main_defined: set[str]
) -> str:
    """Remove function/global definitions from library IR that conflict with main.

    This converts conflicting definitions to declarations so LLVM can link them.

    Args:
        lib_ir: LLVM IR text of the library module.
        main_defined: Set of symbols defined in the main module.

    Returns:
        Modified IR text with conflicting definitions converted to declarations.
    """
    lines = lib_ir.split('\n')
    result_lines = []
    skip_until_close_brace = False
    current_func_name = None

    # Regex patterns
    func_def_pattern = re.compile(r'^define\s+.*?@([a-zA-Z_][a-zA-Z0-9_\.]*|"[^"]+")\s*\(')
    global_def_pattern = re.compile(r'^@([a-zA-Z_][a-zA-Z0-9_\.]*|"[^"]+")\s*=\s*(?!external)')

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Check if we're in a function body to skip
        if skip_until_close_brace:
            if stripped == '}':
                skip_until_close_brace = False
                # Emit a declaration instead
                # We already emitted it when we saw the define
            i += 1
            continue

        # Check for function definition
        func_match = func_def_pattern.match(stripped)
        if func_match:
            func_name = func_match.group(1)
            if func_name.startswith('"') and func_name.endswith('"'):
                func_name = func_name[1:-1]

            if func_name in main_defined:
                # This function is already defined in main, skip its body
                # Convert to declaration: extract signature
                skip_until_close_brace = True

                # Build a declaration from the define line
                # Replace 'define' with 'declare' and remove the body
                # Find the opening brace
                decl_line = stripped
                if '{' in decl_line:
                    decl_line = decl_line[:decl_line.index('{')].strip()
                decl_line = decl_line.replace('define ', 'declare ', 1)
                # Remove attributes that are definition-only
                decl_line = re.sub(r'\s*#\d+', '', decl_line)
                result_lines.append(decl_line)
                i += 1
                continue

        # Check for global variable definition (not external declaration)
        if stripped.startswith('@') and '=' in stripped:
            gv_match = global_def_pattern.match(stripped)
            if gv_match:
                gv_name = gv_match.group(1)
                if gv_name.startswith('"') and gv_name.endswith('"'):
                    gv_name = gv_name[1:-1]

                if gv_name in main_defined:
                    # Skip this global definition - main already has it
                    i += 1
                    continue

        result_lines.append(line)
        i += 1

    return '\n'.join(result_lines)


def link_with_deduplication(
    main_module: llvm.ModuleRef,
    other_modules: list[tuple[llvm.ModuleRef, str]]
) -> llvm.ModuleRef:
    """Link modules while deduplicating conflicting symbol definitions.

    This converts conflicting definitions in secondary modules to declarations
    before linking, allowing LLVM's linker to succeed.

    Args:
        main_module: The main program module (highest priority).
        other_modules: List of (module, name) tuples to link in.

    Returns:
        The main module with other symbols linked in.
    """
    # Get symbols defined in main
    main_defined = get_defined_symbols(main_module)

    for other_mod, name in other_modules:
        # Get IR text of the other module
        other_ir = str(other_mod)

        # Strip conflicting definitions
        cleaned_ir = strip_conflicting_definitions(other_ir, main_defined)

        # Parse the cleaned IR
        try:
            cleaned_mod = llvm.parse_assembly(cleaned_ir)
        except Exception as e:
            # Debug: write IR to file
            debug_path = f'/tmp/sushi_clean_failed_{name}.ll'
            with open(debug_path, 'w') as f:
                f.write(cleaned_ir)
            raise RuntimeError(
                f"Failed to parse cleaned IR for {name}. Debug written to {debug_path}: {e}"
            )

        # Now link should succeed
        try:
            main_module.link_in(cleaned_mod, preserve=False)
        except Exception as e:
            raise RuntimeError(f"Failed to link {name} after deduplication: {e}")

        # Update main_defined with newly added symbols
        main_defined = get_defined_symbols(main_module)

    return main_module


def merge_modules_simple(
    main_module: llvm.ModuleRef,
    library_modules: list[llvm.ModuleRef],
    stdlib_modules: list[llvm.ModuleRef]
) -> llvm.ModuleRef:
    """Simple merge that uses the main module as base.

    This approach deduplicates symbols by stripping conflicting definitions
    from library modules before linking.

    Args:
        main_module: The main program module (highest priority).
        library_modules: User library modules (medium priority).
        stdlib_modules: Standard library modules (lowest priority).

    Returns:
        The main module with library symbols linked in.
    """
    # Build list of modules with names
    other_modules = []
    for i, lib_mod in enumerate(library_modules):
        other_modules.append((lib_mod, f"library_{i}"))
    for i, std_mod in enumerate(stdlib_modules):
        other_modules.append((std_mod, f"stdlib_{i}"))

    return link_with_deduplication(main_module, other_modules)
