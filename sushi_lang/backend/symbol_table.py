"""Symbol table management for two-phase linking."""
from __future__ import annotations
import re
from dataclasses import dataclass
from enum import Enum

import llvmlite.binding as llvm


class SymbolType(Enum):
    """Type of symbol in LLVM module."""
    FUNCTION = "function"
    GLOBAL_VARIABLE = "global"


class SymbolSource(Enum):
    """Source priority for symbol deduplication."""
    MAIN = 1      # Highest priority - main program
    LIBRARY = 2   # Medium priority - user libraries
    STDLIB = 3    # Lower priority - standard library
    RUNTIME = 4   # Lowest priority - runtime functions


RUNTIME_FUNCTIONS = frozenset({
    "utf8_char_count", "llvm_strlen", "strcmp", "strlen",
    "printf", "sprintf", "fprintf", "puts", "putchar", "getchar",
    "memcmp", "memcpy", "memset", "memmove",
    "toupper", "tolower", "isspace", "isdigit", "isalpha", "isalnum",
    "exit", "abort",
    "fopen", "fclose", "fgets", "fgetc", "fputc", "fputs",
    "fread", "fwrite", "fseek", "ftell", "rewind", "feof", "ferror",
    "malloc", "calloc", "realloc", "free",
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh", "exp", "log", "log10", "pow", "sqrt",
    "ceil", "floor", "fabs", "fmod",
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
        """Initialize symbol table."""
        self.module_name = module_name
        self.source = source
        self.symbols: dict[str, SymbolInfo] = {}  # symbol_name -> SymbolInfo
        # Module-level identified-type declarations (`%Point = type {i32, i32}`).
        # These live on the MODULE, not on any symbol, so a per-symbol ir_text never
        # carries them -- but since #257 a user struct is an identified type, so a merged
        # module that omits the declaration leaves `%Point` opaque and every insertvalue
        # through it fails to parse. Collected here so the merger can re-emit them.
        self.type_defs: set[str] = set()

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
    """Convert LLVM linkage enum value to string name."""
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


# A module-level identified-type declaration: `%Point = type { i32, i32 }` or
# `%"List<i32>" = type { ... }`. Anchored at column 0, which is what distinguishes a
# declaration from a *use* inside an indented instruction.
_TYPE_DEF_RE = re.compile(r'^(%[A-Za-z_$.][\w$.]*|%"[^"]*") = type .+$', re.MULTILINE)


def _extract_module_type_defs(module: llvm.ModuleRef) -> set[str]:
    """Collect a module's identified-type declarations from its printed IR."""
    return {m.group(0).strip() for m in _TYPE_DEF_RE.finditer(str(module))}


def extract_symbol_table(
    module: llvm.ModuleRef,
    module_name: str,
    source: SymbolSource
) -> SymbolTable:
    """Extract symbol table from an LLVM module."""
    table = SymbolTable(module_name, source)
    table.type_defs = _extract_module_type_defs(module)

    for func in module.functions:
        if func.name.startswith("llvm."):
            continue

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

    for gvar in module.global_variables:
        if gvar.name.startswith("llvm."):
            continue

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
