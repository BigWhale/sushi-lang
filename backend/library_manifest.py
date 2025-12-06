"""Library manifest generation for .sushilib files.

This module generates metadata manifest files for compiled Sushi libraries.
The manifest contains type definitions, function signatures, and other metadata
needed to link libraries with programs.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from semantics.units import Unit
    from semantics.semantic_analyzer import SemanticAnalyzer


class LibraryManifestGenerator:
    """Generates .sushilib manifest files for compiled libraries."""

    def __init__(self, analyzer: 'SemanticAnalyzer'):
        """Initialize manifest generator.

        Args:
            analyzer: Semantic analyzer with type tables.
        """
        self.analyzer = analyzer
        self.structs = analyzer.structs
        self.enums = analyzer.enums

    def generate(self, units: list['Unit'], output_path: Path) -> None:
        """Generate manifest file for library.

        Args:
            units: Compilation units in library.
            output_path: Path to .sushilib file (e.g., mylib.sushilib).
        """
        from backend.platform_detect import get_current_platform
        from internals.version import _get_versions

        platform = get_current_platform()
        platform_name = "darwin" if platform.is_darwin else "linux" if platform.is_linux else "unknown"
        VERSION = _get_versions()["app"]

        # Extract library name from output path (mylib.sushilib -> mylib)
        library_name = output_path.stem

        manifest = {
            "sushi_lib_version": "1.0",
            "library_name": library_name,
            "compiled_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform_name,
            "compiler_version": VERSION,
            "public_functions": self._extract_public_functions(units),
            "public_constants": self._extract_public_constants(units),
            "structs": self._extract_structs(units),
            "enums": self._extract_enums(units),
            "generic_types": [],  # Future: Phase 5
            "dependencies": self._extract_dependencies(units),
        }

        # Write JSON manifest
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2)
            f.write('\n')  # Trailing newline

    def _extract_public_functions(self, units: list['Unit']) -> list[dict]:
        """Extract public function signatures from units."""
        public_funcs = []

        for unit in units:
            if unit.ast is None:
                continue
            for func in unit.ast.functions:
                if not func.is_public:
                    continue

                public_funcs.append({
                    "name": func.name,
                    "params": [
                        {"name": p.name, "type": self._type_to_string(p.ty)}
                        for p in func.params
                    ],
                    "return_type": self._type_to_string(func.ret),
                    "is_generic": func.type_params is not None and len(func.type_params) > 0,
                    "type_params": [str(tp) for tp in func.type_params] if func.type_params else [],
                })

        return public_funcs

    def _extract_public_constants(self, units: list['Unit']) -> list[dict]:
        """Extract public constants (all constants are public)."""
        public_consts = []

        for unit in units:
            if unit.ast is None:
                continue
            for const in unit.ast.constants:
                public_consts.append({
                    "name": const.name,
                    "type": self._type_to_string(const.ty),
                    # Don't serialize value - just type info for validation
                })

        return public_consts

    def _extract_structs(self, units: list['Unit']) -> list[dict]:
        """Extract struct definitions from units."""
        structs = []
        seen_names = set()

        for unit in units:
            if unit.ast is None:
                continue
            for struct_def in unit.ast.structs:
                if struct_def.name in seen_names:
                    continue
                seen_names.add(struct_def.name)

                structs.append({
                    "name": struct_def.name,
                    "fields": [
                        {"name": field.name, "type": self._type_to_string(field.ty)}
                        for field in struct_def.fields
                    ],
                    "is_generic": False,  # Non-generic structs only for now
                    "type_params": [],
                })

        return structs

    def _extract_enums(self, units: list['Unit']) -> list[dict]:
        """Extract enum definitions from units."""
        enums = []
        seen_names = set()

        for unit in units:
            if unit.ast is None:
                continue
            for enum_def in unit.ast.enums:
                if enum_def.name in seen_names:
                    continue
                seen_names.add(enum_def.name)

                variants = []
                for variant in enum_def.variants:
                    variants.append({
                        "name": variant.name,
                        "has_data": variant.data_type is not None,
                        "data_type": self._type_to_string(variant.data_type) if variant.data_type else None,
                    })

                enums.append({
                    "name": enum_def.name,
                    "variants": variants,
                    "is_generic": False,  # Non-generic enums only for now
                    "type_params": [],
                })

        return enums

    def _extract_dependencies(self, units: list['Unit']) -> list[str]:
        """Extract stdlib dependencies from all units."""
        deps = set()

        for unit in units:
            if unit.ast is None:
                continue
            for use_stmt in unit.ast.uses:
                if use_stmt.is_stdlib:
                    deps.add(use_stmt.path)

        return sorted(deps)

    def _type_to_string(self, ty) -> str:
        """Convert Type object to string representation.

        Examples:
            i32 -> "i32"
            Result<bool, StdError> -> "Result<bool, StdError>"
            i32[] -> "i32[]"
        """
        if ty is None:
            return "~"

        # Use existing Type.__str__ method
        return str(ty)
