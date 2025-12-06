"""Symbol table management for two-phase linking.

This module provides data structures for extracting and managing symbol information
from LLVM modules. It's the first phase of the two-phase linking process that
resolves symbol conflicts between the main program, user libraries, and stdlib.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

import llvmlite.binding as llvm


class SymbolType(Enum):
    """Type of symbol in LLVM module."""
    FUNCTION = "function"
    GLOBAL_VARIABLE = "global"


class SymbolSource(Enum):
    """Source priority for symbol deduplication.

    Lower value = higher priority. Main program definitions take precedence
    over library definitions, which take precedence over stdlib.
    """
    MAIN = 1      # Highest priority - main program
    LIBRARY = 2   # Medium priority - user libraries
    STDLIB = 3    # Lower priority - standard library
    RUNTIME = 4   # Lowest priority - runtime functions


# Runtime functions that are commonly duplicated across modules
RUNTIME_FUNCTIONS = frozenset({
    # String/memory operations
    "utf8_char_count", "llvm_strlen", "strcmp", "strlen",
    # I/O functions
    "printf", "sprintf", "fprintf", "puts", "putchar", "getchar",
    # Memory operations
    "memcmp", "memcpy", "memset", "memmove",
    # Character classification
    "toupper", "tolower", "isspace", "isdigit", "isalpha", "isalnum",
    # Process control
    "exit", "abort",
    # File operations
    "fopen", "fclose", "fgets", "fgetc", "fputc", "fputs",
    "fread", "fwrite", "fseek", "ftell", "rewind", "feof", "ferror",
    # Memory allocation
    "malloc", "calloc", "realloc", "free",
    # Math functions
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh", "exp", "log", "log10", "pow", "sqrt",
    "ceil", "floor", "fabs", "fmod",
    # Time functions
    "time", "nanosleep", "usleep", "sleep",
})


@dataclass
class SymbolInfo:
    """Metadata about a single symbol in a module."""
    name: str
    symbol_type: SymbolType
    is_declaration: bool  # True for external declarations (no body)
    linkage: str          # LLVM linkage type name
    module_name: str      # Which module this symbol came from
    source: SymbolSource  # Priority for deduplication
    ir_text: str | None   # Full IR text for this symbol (if definition)

    def is_definition(self) -> bool:
        """Check if this is a definition (has body) vs declaration."""
        return not self.is_declaration

    def is_runtime_function(self) -> bool:
        """Check if this is a common runtime/libc function."""
        return self.name in RUNTIME_FUNCTIONS

    def is_internal_linkage(self) -> bool:
        """Check if this symbol has internal linkage (not exported)."""
        return self.linkage in ("internal", "private")

    def is_external_linkage(self) -> bool:
        """Check if this symbol has external linkage (exported)."""
        return self.linkage in ("external", "linkonce_odr", "weak_odr")


class SymbolTable:
    """Symbol table for a single LLVM module."""

    def __init__(self, module_name: str, source: SymbolSource):
        """Initialize symbol table.

        Args:
            module_name: Name of the module (e.g., "main", "mylib", "io/stdio").
            source: Priority classification for this module.
        """
        self.module_name = module_name
        self.source = source
        self.symbols: dict[str, SymbolInfo] = {}  # symbol_name -> SymbolInfo

    def add_symbol(self, symbol: SymbolInfo) -> None:
        """Add a symbol to the table."""
        self.symbols[symbol.name] = symbol

    def get_symbol(self, name: str) -> SymbolInfo | None:
        """Look up a symbol by name."""
        return self.symbols.get(name)

    def has_definition(self, name: str) -> bool:
        """Check if this table has a definition (not declaration) for a symbol."""
        symbol = self.symbols.get(name)
        return symbol is not None and symbol.is_definition()

    def get_definitions(self) -> list[SymbolInfo]:
        """Get all symbols that are definitions (not declarations)."""
        return [s for s in self.symbols.values() if s.is_definition()]

    def get_declarations(self) -> list[SymbolInfo]:
        """Get all symbols that are declarations only."""
        return [s for s in self.symbols.values() if s.is_declaration]

    def __repr__(self) -> str:
        defs = len(self.get_definitions())
        decls = len(self.get_declarations())
        return f"SymbolTable({self.module_name}, {self.source.name}, {defs} defs, {decls} decls)"


def _get_linkage_name(linkage_value: int) -> str:
    """Convert LLVM linkage enum value to string name.

    LLVM linkage values (from llvm-c/Core.h):
        0 = external
        1 = available_externally
        2 = linkonce_any
        3 = linkonce_odr
        4 = linkonce_odr_auto_hide (deprecated)
        5 = weak_any
        6 = weak_odr
        7 = appending
        8 = internal
        9 = private
        10 = dllimport
        11 = dllexport
        12 = external_weak
        13 = ghost (deprecated)
        14 = common
        15 = linker_private (deprecated)
        16 = linker_private_weak (deprecated)
    """
    linkage_names = {
        0: "external",
        1: "available_externally",
        2: "linkonce_any",
        3: "linkonce_odr",
        4: "linkonce_odr_auto_hide",
        5: "weak_any",
        6: "weak_odr",
        7: "appending",
        8: "internal",
        9: "private",
        10: "dllimport",
        11: "dllexport",
        12: "external_weak",
        13: "ghost",
        14: "common",
        15: "linker_private",
        16: "linker_private_weak",
    }
    return linkage_names.get(linkage_value, f"unknown({linkage_value})")


def extract_symbol_table(
    module: llvm.ModuleRef,
    module_name: str,
    source: SymbolSource
) -> SymbolTable:
    """Extract symbol table from an LLVM module.

    Args:
        module: Parsed LLVM bitcode module.
        module_name: Name for this module (e.g., "main", "mylib").
        source: Priority classification.

    Returns:
        SymbolTable with all symbols from the module.
    """
    table = SymbolTable(module_name, source)

    # Extract function symbols
    for func in module.functions:
        # Skip LLVM intrinsics - they're handled specially by LLVM
        if func.name.startswith("llvm."):
            continue

        # Get full IR text for this function (both definitions and declarations)
        # Declarations are needed so we can emit proper declare statements
        ir_text = str(func)

        symbol = SymbolInfo(
            name=func.name,
            symbol_type=SymbolType.FUNCTION,
            is_declaration=func.is_declaration,
            linkage=_get_linkage_name(func.linkage),
            module_name=module_name,
            source=source,
            ir_text=ir_text
        )
        table.add_symbol(symbol)

    # Extract global variable symbols
    for gvar in module.global_variables:
        # Skip LLVM internal globals
        if gvar.name.startswith("llvm."):
            continue

        # Get full IR text for this global (both definitions and declarations)
        ir_text = str(gvar)

        symbol = SymbolInfo(
            name=gvar.name,
            symbol_type=SymbolType.GLOBAL_VARIABLE,
            is_declaration=gvar.is_declaration,
            linkage=_get_linkage_name(gvar.linkage),
            module_name=module_name,
            source=source,
            ir_text=ir_text
        )
        table.add_symbol(symbol)

    return table
