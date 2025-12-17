"""Unified library registry for managing library metadata.

This module eliminates duplication between semantic analysis and codegen
by pre-parsing library manifests into typed objects once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from semantics.typesys import StructType, EnumType, EnumVariantInfo

if TYPE_CHECKING:
    from semantics.passes.collect.functions import FuncSig


@dataclass
class LibraryMetadata:
    """Pre-parsed library metadata with typed objects."""

    name: str
    path: Path
    platform: str
    version: str
    functions: dict[str, 'FuncSig'] = field(default_factory=dict)
    structs: dict[str, StructType] = field(default_factory=dict)
    enums: dict[str, EnumType] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    raw_manifest: dict = field(default_factory=dict)


class LibraryRegistry:
    """Central registry for loaded library metadata.

    Provides pre-parsed type information to both semantic analyzer and codegen,
    eliminating duplicate manifest parsing.
    """

    def __init__(self):
        self._libraries: dict[str, LibraryMetadata] = {}
        self._struct_table: dict[str, StructType] = {}
        self._enum_table: dict[str, EnumType] = {}

    def register_library(
        self,
        lib_path: Path,
        manifest: dict,
        struct_table: dict[str, StructType] | None = None,
        enum_table: dict[str, EnumType] | None = None,
    ) -> LibraryMetadata:
        """Register a library and pre-parse its metadata.

        Args:
            lib_path: Path to the .slib file.
            manifest: Raw manifest dictionary from library format.
            struct_table: Current struct table for type resolution.
            enum_table: Current enum table for type resolution.

        Returns:
            Pre-parsed LibraryMetadata.
        """
        lib_name = manifest.get("library_name", lib_path.stem)

        if lib_name in self._libraries:
            return self._libraries[lib_name]

        self._struct_table = struct_table or {}
        self._enum_table = enum_table or {}

        metadata = LibraryMetadata(
            name=lib_name,
            path=lib_path,
            platform=manifest.get("platform", "unknown"),
            version=manifest.get("version", "0.0.0"),
            dependencies=manifest.get("dependencies", []),
            raw_manifest=manifest,
        )

        metadata.structs = self._parse_structs(manifest.get("structs", []))
        self._struct_table.update(metadata.structs)

        metadata.enums = self._parse_enums(manifest.get("enums", []))
        self._enum_table.update(metadata.enums)

        metadata.functions = self._parse_functions(manifest.get("public_functions", []))

        self._libraries[lib_name] = metadata
        return metadata

    def _parse_structs(self, struct_list: list[dict]) -> dict[str, StructType]:
        """Parse struct definitions from manifest."""
        from semantics.type_resolution import parse_type_string

        result = {}
        for struct_info in struct_list:
            struct_name = struct_info["name"]
            fields = []
            for f in struct_info.get("fields", []):
                field_type = parse_type_string(
                    f["type"],
                    self._struct_table,
                    self._enum_table
                )
                fields.append((f["name"], field_type))

            result[struct_name] = StructType(name=struct_name, fields=tuple(fields))
        return result

    def _parse_enums(self, enum_list: list[dict]) -> dict[str, EnumType]:
        """Parse enum definitions from manifest."""
        from semantics.type_resolution import parse_type_string

        result = {}
        for enum_info in enum_list:
            enum_name = enum_info["name"]
            variants = []
            for v in enum_info.get("variants", []):
                assoc_types: tuple = ()
                if v.get("has_data") and v.get("data_type"):
                    data_type = parse_type_string(
                        v["data_type"],
                        self._struct_table,
                        self._enum_table
                    )
                    assoc_types = (data_type,)

                variants.append(EnumVariantInfo(name=v["name"], associated_types=assoc_types))

            result[enum_name] = EnumType(name=enum_name, variants=tuple(variants))
        return result

    def _parse_functions(self, func_list: list[dict]) -> dict[str, 'FuncSig']:
        """Parse function signatures from manifest."""
        from semantics.passes.collect.functions import FuncSig, Param
        from semantics.type_resolution import parse_type_string

        result = {}
        for func_info in func_list:
            func_name = func_info["name"]

            params = []
            for idx, p in enumerate(func_info.get("params", [])):
                param_type = parse_type_string(
                    p["type"],
                    self._struct_table,
                    self._enum_table
                )
                params.append(Param(
                    name=p["name"],
                    ty=param_type,
                    name_span=None,
                    type_span=None,
                    index=idx
                ))

            ret_type_str = func_info.get("return_type", "~")
            ret_type = parse_type_string(
                ret_type_str,
                self._struct_table,
                self._enum_table
            )

            result[func_name] = FuncSig(
                name=func_name,
                loc=None,
                name_span=None,
                ret_type=ret_type,
                ret_span=None,
                params=params,
                is_public=True,
            )

        return result

    def get_library(self, lib_name: str) -> LibraryMetadata | None:
        """Get pre-parsed library metadata by name."""
        return self._libraries.get(lib_name)

    def get_all_libraries(self) -> dict[str, LibraryMetadata]:
        """Get all registered libraries."""
        return self._libraries

    def get_all_functions(self) -> dict[str, 'FuncSig']:
        """Get all functions from all libraries (name -> FuncSig)."""
        result = {}
        for lib in self._libraries.values():
            result.update(lib.functions)
        return result

    def get_all_structs(self) -> dict[str, StructType]:
        """Get all structs from all libraries (name -> StructType)."""
        result = {}
        for lib in self._libraries.values():
            result.update(lib.structs)
        return result

    def get_all_enums(self) -> dict[str, EnumType]:
        """Get all enums from all libraries (name -> EnumType)."""
        result = {}
        for lib in self._libraries.values():
            result.update(lib.enums)
        return result

    def clear(self) -> None:
        """Clear all registered libraries."""
        self._libraries.clear()
        self._struct_table.clear()
        self._enum_table.clear()
