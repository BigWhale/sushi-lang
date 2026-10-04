"""Unified library registry for managing library metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sushi_lang.semantics.typesys import StructType, EnumType, EnumVariantInfo

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import FuncSig


def library_unit(lib_name: str, unit: str | None) -> str:
    """The unit a compiled library's record is declared in, as `lib/<library>/<unit>`.

    The name a source library's injected unit has, so one unit-keyed table and one
    scope serve both kinds of library (#1120). `unit` is the record's `unit` key; a
    record with none is of the library's only unit, which has the library's name.
    """
    return f"lib/{lib_name}/{unit or lib_name}"


def manifest_reexports(manifest: dict) -> tuple[dict, ...]:
    """One library's `public use` records: `{"unit", "path", "kind"}` each (#585).

    The ONE reader of the key. Two readers ask, and neither can ask the other's
    question: the `namespaces` pass composes the unit's namespace from what it names,
    and the driver has to put a re-exported stdlib module into the consumer's build at
    all -- a source library's `use` line does that by being text, and a compiled one
    has only this record. A manifest written before the key reads as no re-export,
    which is what an older compiled library has: the statement was refused (CE3514).
    """
    return tuple((manifest or {}).get("reexports") or ())


def reexported_libraries(manifest: dict) -> tuple[str, ...]:
    """The `lib/...` paths one compiled library re-exports (#1106).

    The consumer's build loads each one as if it wrote the `use` itself, and links its
    bitcode, because no unit of the consumer names it.
    """
    return tuple(record["path"] for record in manifest_reexports(manifest)
                 if record.get("kind") == "library" and record.get("path"))


def manifest_dependencies(manifest: dict) -> tuple[dict, ...]:
    """One library's `dependencies` records: `{"path", "kind", ...}` each (#1120).

    The ONE reader of the key. A `stdlib` record is a `use <module>` of the library's
    own units; a `library` record is a `use <lib/...>`, plain or public, with the
    `library_name` and the `library_version` that the library's build found.
    """
    return tuple((manifest or {}).get("dependencies") or ())


def library_dependencies(manifest: dict) -> tuple[dict, ...]:
    """The `library` records of `dependencies`: what the consumer's build must load."""
    return tuple(record for record in manifest_dependencies(manifest)
                 if record.get("kind") == "library" and record.get("path"))


def stdlib_dependencies(manifest: dict) -> tuple[str, ...]:
    """The stdlib module paths of `dependencies`."""
    return tuple(record["path"] for record in manifest_dependencies(manifest)
                 if record.get("kind") == "stdlib" and record.get("path"))


def parse_signature(func_info: dict, struct_table: dict, enum_table: dict,
                    owner: str | None = None,
                    lib_path: Path | str | None = None) -> 'FuncSig':
    """One manifest signature record as a `FuncSig`: the ONE reader of a signature.

    A function, a closure helper and an extension method all carry the same four keys
    (`signature_record`), so they are read here and nowhere else.

    A record states its channel twice: `has_channel`, and the `error_type` and
    `return_type` it implies. A record whose two answers disagree is damaged, CE3512.
    That the field is there at all is `check_manifest`'s rule, not this reader's.
    """
    from sushi_lang.internals.diagnostics import SushiError
    from sushi_lang.semantics.channel import has_channel
    from sushi_lang.semantics.param_modes import ParamMode
    from sushi_lang.semantics.passes.collect.functions import FuncSig, Param
    from sushi_lang.semantics.type_resolution import parse_type_string

    func_name = func_info["name"]
    params = [
        Param(name=p["name"], ty=parse_type_string(p["type"], struct_table, enum_table),
              name_span=None, type_span=None, index=idx,
              is_nom=p.get("mode") == ParamMode.NOM.value)
        for idx, p in enumerate(func_info.get("params", []))
    ]
    ret_type = parse_type_string(func_info.get("return_type", "~"), struct_table, enum_table)
    # The channel the declaration spelled (`| E`, #541). Absent is a bare function, or an
    # explicit Result return that carries its arms in `return_type`.
    err_type_str = func_info.get("error_type")
    err_type = (parse_type_string(err_type_str, struct_table, enum_table)
                if err_type_str else None)

    sig = FuncSig(
        name=func_name,
        loc=None,
        name_span=None,
        ret_type=ret_type,
        ret_span=None,
        params=params,
        is_public=owner is None,
        unit_name=owner,
        err_type=err_type,
        link_symbol=func_info.get("link_symbol"),
    )
    stated = func_info.get("has_channel")
    if stated is not None and stated != has_channel(sig):
        raise SushiError(
            "CE3512", path=str(lib_path),
            reason=f"function '{func_name}' states has_channel {str(stated).lower()}, "
                   "and its error_type and return_type say otherwise")
    return sig


@dataclass
class LibraryMetadata:
    """Pre-parsed library metadata with typed objects."""

    name: str
    path: Path
    platform: str
    functions: dict[str, 'FuncSig'] = field(default_factory=dict)
    # The unit that exports each public function, as `lib/<library>/<unit>`: the name
    # a source library's injected unit has. CW3002 names it (#1103).
    export_units: dict[str, str] = field(default_factory=dict)
    # Export-closure private helpers (C4b/C5): signature-only records whose
    # definitions link from the library bitcode. Kept separate from
    # `functions` because the consumer applies clash (CE5007), not
    # local-wins, semantics to them.
    private_functions: dict[tuple[str, str], 'FuncSig'] = field(default_factory=dict)
    # What the library declares and does NOT export (#469): name -> kind, and nothing
    # else. No signature travels with them, so they are not callables the consumer can
    # register -- they exist so the CE3005 gate can say "private" where it used to have
    # to say "undefined". Five kinds now: a function, a generic function, a struct, an
    # enum and a constant. A type is the one the type funnel asks about, because a kept
    # type reaches no table and "unknown type" was the wrong word for it.
    not_exported: dict[str, str] = field(default_factory=dict)
    # The same records as (unit, name, kind), one for each unit that keeps the name
    # (#1112). CE5013 reads the unit to know which symbol the bitcode defines. The unit
    # is None for a record of a library that was built before the record had one.
    kept: tuple[tuple[str | None, str, str], ...] = ()
    structs: dict[str, StructType] = field(default_factory=dict)
    enums: dict[str, EnumType] = field(default_factory=dict)
    # The `dependencies` records (#1120): every module and library the units use.
    dependencies: tuple[dict, ...] = ()
    # What each of the library's units re-exports (#585): the target of every
    # `public use`, keyed by the unit that wrote it. A compiled library ships no text
    # for the `namespaces` pass to read the statement from.
    reexports: tuple[dict, ...] = ()
    raw_manifest: dict = field(default_factory=dict)


class LibraryRegistry:
    """Central registry for loaded library metadata."""

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
        """Register a library and pre-parse its metadata."""
        lib_name = manifest.get("library_name", lib_path.stem)

        if lib_name in self._libraries:
            return self._libraries[lib_name]

        self._struct_table = struct_table or {}
        self._enum_table = enum_table or {}

        metadata = LibraryMetadata(
            name=lib_name,
            path=lib_path,
            platform=manifest.get("platform", "unknown"),
            dependencies=manifest_dependencies(manifest),
            reexports=manifest_reexports(manifest),
            raw_manifest=manifest,
        )

        metadata.structs = self._parse_structs(manifest.get("structs", []))
        self._struct_table.update(metadata.structs)

        metadata.enums = self._parse_enums(manifest.get("enums", []))
        self._enum_table.update(metadata.enums)

        metadata.functions = self._parse_functions(manifest.get("public_functions", []),
                                                   lib_path=lib_path)
        metadata.export_units = {
            func_info["name"]: library_unit(lib_name, func_info.get("unit"))
            for func_info in manifest.get("public_functions", []) or []
        }

        # Keyed (unit, name): two of the library's own units may each ship a
        # private `helper`, and each record names its unit (#494). The key wears the
        # `lib/<library>/<unit>` prefix a SOURCE library's injected units wear, so a
        # consumer unit of the same name cannot collide with it in any per-unit table.
        templates = manifest.get("templates") or {}
        for func_info in templates.get("private_functions", []) or []:
            unit = library_unit(lib_name, func_info.get("unit"))
            parsed = self._parse_functions([func_info], owner=unit, lib_path=lib_path)
            metadata.private_functions[(unit, func_info["name"])] = parsed[func_info["name"]]

        kept_records = manifest.get("not_exported", []) or []
        metadata.not_exported = {
            record["name"]: record.get("kind", "function") for record in kept_records
        }
        metadata.kept = tuple(
            (record.get("unit"), record["name"], record.get("kind", "function"))
            for record in kept_records)

        self._libraries[lib_name] = metadata
        return metadata

    def _parse_structs(self, struct_list: list[dict]) -> dict[str, StructType]:
        """Parse struct definitions from manifest."""
        from sushi_lang.semantics.type_resolution import parse_type_string

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
        from sushi_lang.semantics.type_resolution import parse_type_string

        result = {}
        for enum_info in enum_list:
            enum_name = enum_info["name"]
            variants = []
            for v in enum_info.get("variants", []):
                assoc_types = tuple(
                    parse_type_string(t, self._struct_table, self._enum_table)
                    for t in (v.get("data_types") or []) if v.get("has_data"))

                variants.append(EnumVariantInfo(name=v["name"], associated_types=assoc_types))

            result[enum_name] = EnumType(name=enum_name, variants=tuple(variants),
                                         is_error=bool(enum_info.get("is_error", False)))
        return result

    def _parse_functions(self, func_list: list[dict], owner: str | None = None,
                         lib_path: Path | None = None) -> dict[str, 'FuncSig']:
        """Parse function signatures from manifest.

        `owner` names the library for a record that is NOT part of its API: the export
        closure ships it so the library's own bodies can call it, and the signature says
        so, so that the CE3005 gate answers for it like any other private function.
        """
        return {func_info["name"]: parse_signature(func_info, self._struct_table,
                                                   self._enum_table, owner, lib_path)
                for func_info in func_list}

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

    def get_all_private_functions(self) -> dict[tuple[str, str], tuple[str, 'FuncSig']]:
        """Every export-closure private function ((unit, name) -> (lib_name, FuncSig))."""
        result = {}
        for lib in self._libraries.values():
            for key, sig in lib.private_functions.items():
                result[key] = (lib.name, sig)
        return result

    def get_all_not_exported(self) -> dict[str, tuple[str, str]]:
        """Every kept name from every library (name -> (lib_name, kind))."""
        result = {}
        for lib in self._libraries.values():
            for name, kind in lib.not_exported.items():
                result[name] = (lib.name, kind)
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

