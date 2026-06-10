"""Library manifest generation for .slib files.

This module generates binary library files (.slib) for compiled Sushi libraries.
The format combines LLVM bitcode with MessagePack-encoded metadata in a single file.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit
    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer


class LibraryManifestGenerator:
    """Generates .slib library files."""

    def __init__(self, analyzer: 'SemanticAnalyzer'):
        """Initialize manifest generator.

        Args:
            analyzer: Semantic analyzer with type tables.
        """
        self.analyzer = analyzer
        self.structs = analyzer.structs
        self.enums = analyzer.enums

    def generate(self, units: list['Unit'], output_path: Path, bitcode: bytes) -> None:
        """Generate .slib library file.

        Args:
            units: Compilation units in library.
            output_path: Path to .slib file (e.g., mylib.slib).
            bitcode: LLVM bitcode bytes.
        """
        from sushi_lang.backend.platform_detect import get_current_platform
        from sushi_lang.internals.version import _get_versions
        from sushi_lang.backend.library_format import LibraryFormat

        platform = get_current_platform()
        platform_name = "darwin" if platform.is_darwin else "linux" if platform.is_linux else "unknown"
        VERSION = _get_versions()["app"]

        # Extract library name from output path (mylib.slib -> mylib)
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
            "templates": self._extract_templates(units),
            "dependencies": self._extract_dependencies(units),
        }

        # Write .slib binary format
        LibraryFormat.write(output_path, manifest, bitcode)

    def _contains_foreign_ptr(self, ty) -> bool:
        """Recursively check whether a type exposes a foreign `ptr` (ForeignPtrType)."""
        from sushi_lang.semantics.typesys import (
            ForeignPtrType, ArrayType, DynamicArrayType, ReferenceType,
            PointerType, ResultType, IteratorType, StructType, EnumType,
        )
        if ty is None:
            return False
        if isinstance(ty, ForeignPtrType):
            return True
        if isinstance(ty, (ArrayType, DynamicArrayType)):
            return self._contains_foreign_ptr(ty.base_type)
        if isinstance(ty, ReferenceType):
            return self._contains_foreign_ptr(ty.referenced_type)
        if isinstance(ty, PointerType):
            return self._contains_foreign_ptr(ty.pointee_type)
        if isinstance(ty, ResultType):
            return self._contains_foreign_ptr(ty.ok_type) or self._contains_foreign_ptr(ty.err_type)
        if isinstance(ty, IteratorType):
            return self._contains_foreign_ptr(ty.element_type)
        if isinstance(ty, StructType):
            return any(self._contains_foreign_ptr(ft) for _, ft in ty.fields)
        if isinstance(ty, EnumType):
            return any(
                self._contains_foreign_ptr(at)
                for v in ty.variants for at in v.associated_types
            )
        return False

    def _extract_public_functions(self, units: list['Unit']) -> list[dict]:
        """Extract public function signatures from units.

        CE5002: a public function whose signature exposes a foreign `ptr` cannot
        appear in a library public API - FFI is a private unit detail. Detecting
        one aborts the .slib write (no partial artifact).
        """
        import sushi_lang.internals.errors as er

        public_funcs = []

        for unit in units:
            if unit.ast is None:
                continue
            for func in unit.ast.functions:
                if not func.is_public:
                    continue

                # Generic functions are NOT concrete callables. They ship only as
                # instantiable templates (templates.generic_functions) and are
                # monomorphized at the consumer's call site. Emitting them here too
                # produced a bogus concrete FuncSig with unresolved type params and
                # forced defensive consumer-side skips. Route them to templates only.
                if func.type_params:
                    continue

                # CE0116: reject a v1 NATIVE variadic '...T' function in a public
                # library signature. A native variadic collects its trailing args
                # into a runtime T[] inside a single concrete function -- there is
                # no template to monomorphize at the consumer, so it genuinely
                # cannot cross the .slib boundary. This is distinct from a v2 type
                # pack '...Ts': a pack function carries type_params and was already
                # routed to templates.generic_functions above (the consumer
                # monomorphizes it per call site), so it never reaches here. The
                # discriminator is is_variadic (v1, blocked) vs is_pack (v2,
                # allowed-as-template), NOT the '...' spelling they share.
                if any(getattr(p, "is_variadic", False) for p in func.params):
                    er.emit(self.analyzer.reporter, er.ERR.CE0116,
                            getattr(func, "name_span", None) or func.loc, name=func.name)
                    raise ValueError(
                        f"CE0116: public function '{func.name}' is variadic and "
                        f"cannot appear in a library public API"
                    )

                # CE5002: reject foreign `ptr` in a public library signature.
                exposes_ptr = self._contains_foreign_ptr(func.ret) or any(
                    self._contains_foreign_ptr(p.ty) for p in func.params
                )
                if exposes_ptr:
                    er.emit(self.analyzer.reporter, er.ERR.CE5002,
                            getattr(func, "name_span", None) or func.loc, name=func.name)
                    raise ValueError(
                        f"CE5002: public function '{func.name}' exposes a foreign `ptr` "
                        f"and cannot appear in a library public API"
                    )

                public_funcs.append({
                    "name": func.name,
                    "params": [
                        {"name": p.name, "type": self._type_to_string(p.ty)}
                        for p in func.params
                    ],
                    "return_type": self._type_to_string(func.ret),
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
                # Generic structs ship as re-parsable templates (see
                # _extract_templates), never as concrete entries.
                if struct_def.type_params:
                    continue
                if struct_def.name in seen_names:
                    continue
                seen_names.add(struct_def.name)

                structs.append({
                    "name": struct_def.name,
                    "fields": [
                        {"name": field.name, "type": self._type_to_string(field.ty)}
                        for field in struct_def.fields
                    ],
                    "is_generic": False,
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
                # Generic enums ship as re-parsable templates (see
                # _extract_templates), never as concrete entries.
                if enum_def.type_params:
                    continue
                if enum_def.name in seen_names:
                    continue
                seen_names.add(enum_def.name)

                variants = []
                for variant in enum_def.variants:
                    has_data = len(variant.associated_types) > 0
                    data_type = self._type_to_string(variant.associated_types[0]) if has_data else None
                    variants.append({
                        "name": variant.name,
                        "has_data": has_data,
                        "data_type": data_type,
                    })

                enums.append({
                    "name": enum_def.name,
                    "variants": variants,
                    "is_generic": False,
                    "type_params": [],
                })

        return enums

    def _collect_private_symbols(self, units: list['Unit']) -> set[str]:
        """Collect names of library-PRIVATE top-level symbols across all units.

        A symbol is library-private when it is a function declared without
        `public`, or any struct / enum / constant (these have no public marker
        and therefore never cross the .slib boundary in this phase). A public
        generic body may freely reference its own parameters, language builtins,
        and other PUBLIC symbols; referencing any of these private names would
        be unresolvable at the consumer and aborts the export (CE5006).
        """
        private: set[str] = set()
        for unit in units:
            if unit.ast is None:
                continue
            for fn in unit.ast.functions:
                if not getattr(fn, "is_public", False):
                    private.add(fn.name)
            for s in unit.ast.structs:
                private.add(s.name)
            for e in unit.ast.enums:
                private.add(e.name)
            for c in unit.ast.constants:
                private.add(c.name)
        return private

    def _scan_referenced_symbols(self, node, acc: set[str]) -> None:
        """Walk a body AST collecting referenced free symbol names.

        Collects `Name.id`, `Call`/`DotCall`/`MethodCall`/`MemberAccess`
        targets, and struct/enum constructor type names. This is a pragmatic,
        conservative scan: it over-collects (it does not subtract local `let`
        bindings or parameters), which is safe because the caller only rejects a
        reference that matches a known library-private symbol. It does not need
        to classify every reference - only to surface private-symbol uses.
        """
        from sushi_lang.semantics import ast as A

        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                self._scan_referenced_symbols(item, acc)
            return

        if isinstance(node, A.Name):
            acc.add(node.id)
        elif isinstance(node, A.Call):
            callee = node.callee
            if isinstance(callee, A.Name):
                acc.add(callee.id)
        elif isinstance(node, A.StructConstructor):
            acc.add(node.struct_name)
        elif isinstance(node, A.EnumConstructor):
            acc.add(node.enum_name)

        # Recurse into dataclass fields (AST nodes are dataclasses).
        import dataclasses
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for f in dataclasses.fields(node):
                self._scan_referenced_symbols(getattr(node, f.name, None), acc)

    def _scan_referenced_type_names(self, node, acc: set[str]) -> None:
        """Walk a declaration collecting referenced user-TYPE names.

        Surfaces ``UnknownType.name`` and ``GenericTypeRef.base_name`` (the two
        ways a struct field or enum variant payload names another type),
        recursing through dataclass fields and sequences so nested generics
        (``Own<Tree<T>>``, ``Inner<T>``) are reached. Like
        ``_scan_referenced_symbols`` this over-collects; the caller only rejects
        names matching a known library-private symbol.
        """
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef

        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                self._scan_referenced_type_names(item, acc)
            return

        if isinstance(node, UnknownType):
            acc.add(node.name)
        elif isinstance(node, GenericTypeRef):
            acc.add(node.base_name)

        import dataclasses
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for f in dataclasses.fields(node):
                self._scan_referenced_type_names(getattr(node, f.name, None), acc)

    def _check_generic_export_closure(
        self, func, private_symbols: set[str], allowed: set[str] = frozenset()
    ) -> None:
        """Reject (CE5006) a public generic whose body references a private symbol.

        Bounds this milestone to self-contained generics: the re-parsed template
        is monomorphized at the consumer, which has no visibility into the
        library's private functions/types/constants. ``allowed`` lists co-shipped
        public templates (generic struct/enum names) that a body may freely
        reference even though they also appear in ``private_symbols``.
        """
        import sushi_lang.internals.errors as er

        referenced: set[str] = set()
        if func.body is not None:
            self._scan_referenced_symbols(func.body, referenced)

        # A generic may reference its own type-param names as type annotations;
        # those are never private symbols, but exclude them defensively.
        type_param_names = {tp.name for tp in (func.type_params or [])}
        param_names = {p.name for p in func.params}

        offending = sorted(
            referenced & private_symbols
            - type_param_names - param_names - set(allowed)
        )
        if offending:
            symbol = offending[0]
            er.emit(self.analyzer.reporter, er.ERR.CE5006,
                    getattr(func, "name_span", None) or func.loc,
                    name=func.name, symbol=symbol)
            raise ValueError(
                f"CE5006: public generic '{func.name}' references "
                f"library-private symbol '{symbol}' and cannot be exported"
            )

    def _check_generic_type_export_closure(
        self, node, private_symbols: set[str], allowed: set[str] = frozenset()
    ) -> None:
        """Reject (CE5006) a public generic struct/enum whose field or variant
        payload types reference a library-private symbol.

        Mirrors ``_check_generic_export_closure`` for type definitions. A
        field/payload may reference the generic's own name (recursion, e.g.
        ``Own<Tree<T>>``) and any co-shipped public template in ``allowed``; a
        reference to a concrete library-private type aborts the export.
        """
        import sushi_lang.internals.errors as er

        referenced: set[str] = set()
        self._scan_referenced_type_names(node, referenced)

        type_param_names = {tp.name for tp in (node.type_params or [])}

        offending = sorted(
            referenced & private_symbols
            - type_param_names - set(allowed) - {node.name}
        )
        if offending:
            symbol = offending[0]
            er.emit(self.analyzer.reporter, er.ERR.CE5006,
                    getattr(node, "name_span", None) or node.loc,
                    name=node.name, symbol=symbol)
            raise ValueError(
                f"CE5006: public generic '{node.name}' references "
                f"library-private symbol '{symbol}' and cannot be exported"
            )

    def _extract_templates(self, units: list['Unit']) -> dict:
        """Extract instantiable public generic templates (re-parsable source).

        Returns the manifest `templates` section. We ship:

        - `generic_functions`: instantiable public generic function bodies.
        - `generic_structs` / `generic_enums`: instantiable public generic
          struct/enum templates. Unlike functions there is no `public` keyword
          for types, so every generic struct/enum is exported (matching how
          concrete structs/enums already all cross the boundary).
        - `perks`: the DEFINITIONS (method-signature contracts) of every perk
          named by an exported generic's type-parameter constraints. Shipping
          the contract frees the consumer from redeclaring a perk it does not
          author. Only the referenced (minimal, correct) set is shipped.

        `perk_impls` is intentionally left empty: shipping perk implementations
        (`extend T with Perk`) is out of scope (transitive-symbol linkage); the
        consumer provides impls for its own instantiation types.
        """
        from sushi_lang.backend.library_templates import (
            serialize_generic_function, serialize_generic_struct,
            serialize_generic_enum, serialize_perk,
        )

        private_symbols = self._collect_private_symbols(units)

        # Names of every generic struct/enum we ship as a template. A co-shipped
        # template may be referenced by another generic's field/body even though
        # it also appears in `private_symbols` (it resolves at the consumer).
        exported_generic_types: set[str] = set()
        for unit in units:
            if unit.ast is None:
                continue
            for s in unit.ast.structs:
                if s.type_params:
                    exported_generic_types.add(s.name)
            for e in unit.ast.enums:
                if e.type_params:
                    exported_generic_types.add(e.name)

        generic_functions: list[dict] = []
        generic_structs: list[dict] = []
        generic_enums: list[dict] = []
        referenced_perks: set[str] = set()

        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for func in unit.ast.functions:
                if not (func.is_public and func.type_params):
                    continue
                self._check_generic_export_closure(
                    func, private_symbols, exported_generic_types)
                record = serialize_generic_function(func, source)
                generic_functions.append(record)
                referenced_perks.update(record.get("free_perks", []))
            for struct in unit.ast.structs:
                if not struct.type_params:
                    continue
                self._check_generic_type_export_closure(
                    struct, private_symbols, exported_generic_types)
                record = serialize_generic_struct(struct, source)
                generic_structs.append(record)
                referenced_perks.update(record.get("free_perks", []))
            for enum in unit.ast.enums:
                if not enum.type_params:
                    continue
                self._check_generic_type_export_closure(
                    enum, private_symbols, exported_generic_types)
                record = serialize_generic_enum(enum, source)
                generic_enums.append(record)
                referenced_perks.update(record.get("free_perks", []))

        # Ship only the perk DEFINITIONS referenced by an exported generic's
        # constraints, de-duplicated by name (a perk is defined once).
        perks: list[dict] = []
        seen_perks: set[str] = set()
        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for perk in unit.ast.perks:
                if perk.name not in referenced_perks or perk.name in seen_perks:
                    continue
                seen_perks.add(perk.name)
                perks.append(serialize_perk(perk, source))

        return {
            "version": 2,
            "generic_functions": generic_functions,
            "generic_structs": generic_structs,
            "generic_enums": generic_enums,
            "perks": perks,
            "perk_impls": [],
        }

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
        """Convert Type object to string representation."""
        if ty is None:
            return "~"

        return str(ty)
