"""Library manifest generation for .slib files."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sushi_lang.semantics.param_modes import param_mode

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit
    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer


class LibraryManifestGenerator:
    """Generates .slib library files."""

    def __init__(self, analyzer: 'SemanticAnalyzer'):
        """Initialize manifest generator."""
        self.analyzer = analyzer
        self.structs = analyzer.structs
        self.enums = analyzer.enums

    def generate(self, units: list['Unit'], output_path: Path, bitcode: bytes,
                 templates: dict | None = None) -> None:
        """Generate .slib library file."""
        from sushi_lang.backend.platform_detect import current_platform_name
        from sushi_lang.internals.version import _get_versions
        from sushi_lang.backend.library_format import LibraryFormat

        platform_name = current_platform_name()
        VERSION = _get_versions()["app"]

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
            "templates": templates if templates is not None else self._extract_templates(units),
            "dependencies": self._extract_dependencies(units),
        }

        LibraryFormat.write(output_path, manifest, bitcode)

    def _contains_foreign_ptr(self, ty) -> bool:
        """Recursively check whether a type exposes a foreign `ptr` (ForeignPtrType)."""
        from sushi_lang.semantics.type_predicates import contains_foreign_ptr
        return contains_foreign_ptr(ty)

    def _extract_public_functions(self, units: list['Unit']) -> list[dict]:
        """Extract public function signatures from units."""
        import sushi_lang.internals.errors as er

        public_funcs = []

        for unit in units:
            if unit.ast is None:
                continue
            for func in unit.ast.functions:
                if not func.is_public:
                    continue

                # A generic function is not a concrete callable: it ships only as a
                # template and monomorphizes at the consumer. Emitting one here produced a
                # FuncSig with unresolved type params.
                if func.type_params:
                    continue

                # CE0116. A v1 native `...T` collects its trailing args into a runtime T[]
                # in ONE concrete function, so there is no template to monomorphize at the
                # consumer. A v2 pack `...Ts` carries type_params and left through the
                # template route above. The discriminator is is_variadic vs is_pack, NOT
                # the `...` spelling they share.
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
                        # The MODE is its own field, not part of the type string. A
                        # `nom` cannot be spelled in a type at all, and reading peek /
                        # poke back out of a type string was the half that was missing
                        # (docs/design/borrow-model.md S10).
                        {"name": p.name, "type": self._type_to_string(p.ty),
                         "mode": param_mode(p).value}
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

    def _scan_referenced_symbols(self, node, acc: set[str]) -> None:
        """Walk a body AST collecting referenced free symbol names."""
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
        elif isinstance(node, A.EnumConstructor):
            acc.add(node.enum_name)

        import dataclasses
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for f in dataclasses.fields(node):
                self._scan_referenced_symbols(getattr(node, f.name, None), acc)

    def _scan_referenced_type_names(self, node, acc: set[str]) -> None:
        """Walk a declaration collecting referenced user-TYPE names."""
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

    def _compute_export_closure(self, units: list['Unit'], exported: list) -> dict:
        """Walk every exported generic and collect the library-private symbols its body
        (transitively) depends on - the EXPORT CLOSURE (C4b/C5).
        """
        import sushi_lang.internals.errors as er

        priv_concrete_fns: dict[str, tuple] = {}
        priv_generic_fns: dict[str, tuple] = {}
        constants: dict[str, tuple] = {}
        types_by_name: dict[str, tuple] = {}
        external_namespaces: set[str] = set()

        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for fn in unit.ast.functions:
                if fn.type_params:
                    if not getattr(fn, "is_public", False):
                        priv_generic_fns[fn.name] = (fn, source)
                elif not getattr(fn, "is_public", False):
                    priv_concrete_fns[fn.name] = (fn, source)
            for c in unit.ast.constants:
                constants[c.name] = (c, source)
            for s in unit.ast.structs:
                types_by_name[s.name] = (s, source)
            for e in unit.ast.enums:
                types_by_name[e.name] = (e, source)
            for ext in getattr(unit.ast, "externals", None) or []:
                external_namespaces.add(ext.namespace)

        shipped_fns: dict[str, tuple] = {}
        shipped_generic_fns: dict[str, tuple] = {}
        shipped_consts: dict[str, tuple] = {}
        visited: set[str] = set()

        def _reject(root, symbol: str) -> None:
            er.emit(self.analyzer.reporter, er.ERR.CE5006,
                    getattr(root, "name_span", None) or root.loc,
                    name=root.name, symbol=symbol)
            raise ValueError(
                f"CE5006: public generic '{root.name}' references "
                f"un-shippable library symbol '{symbol}' and cannot be exported"
            )

        def _walk(node, root) -> None:
            refs: set[str] = set()
            self._scan_referenced_symbols(node, refs)
            self._scan_referenced_type_names(node, refs)

            own = {getattr(node, "name", None)}
            own |= {tp.name for tp in (getattr(node, "type_params", None) or [])}
            own |= {p.name for p in (getattr(node, "params", None) or [])}

            for name in sorted(refs - own - visited):
                if name in external_namespaces:
                    _reject(root, name)
                elif name in priv_concrete_fns:
                    fn, src = priv_concrete_fns[name]
                    if any(getattr(p, "is_variadic", False) for p in fn.params):
                        _reject(root, name)
                    if self._contains_foreign_ptr(fn.ret) or any(
                        self._contains_foreign_ptr(p.ty) for p in fn.params
                    ):
                        _reject(root, name)
                    visited.add(name)
                    shipped_fns[name] = (fn, src)
                    _walk(fn, root)
                elif name in priv_generic_fns:
                    fn, src = priv_generic_fns[name]
                    visited.add(name)
                    shipped_generic_fns[name] = (fn, src)
                    _walk(fn, root)
                elif name in constants:
                    c, src = constants[name]
                    visited.add(name)
                    shipped_consts[name] = (c, src)
                    _walk(c, root)
                elif name in types_by_name:
                    tnode, _src = types_by_name[name]
                    visited.add(name)
                    _walk(tnode, root)

        for node, _source in exported:
            _walk(node, node)

        return {
            "private_functions": list(shipped_fns.values()),
            "private_generic_functions": list(shipped_generic_fns.values()),
            "constants": list(shipped_consts.values()),
        }

    def _extract_templates(self, units: list['Unit']) -> dict:
        """Extract instantiable public generic templates (re-parsable source)."""
        from sushi_lang.semantics.library_templates import (
            serialize_generic_function, serialize_generic_struct,
            serialize_generic_enum, serialize_perk, serialize_perk_impl,
            slice_decl_source,
        )

        generic_functions: list[dict] = []
        generic_structs: list[dict] = []
        generic_enums: list[dict] = []
        referenced_perks: set[str] = set()
        exported: list[tuple] = []

        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for func in unit.ast.functions:
                if not (func.is_public and func.type_params):
                    continue
                exported.append((func, source))
                record = serialize_generic_function(func, source)
                generic_functions.append(record)
                referenced_perks.update(record.get("free_perks", []))
            for struct in unit.ast.structs:
                if not struct.type_params:
                    continue
                exported.append((struct, source))
                record = serialize_generic_struct(struct, source)
                generic_structs.append(record)
                referenced_perks.update(record.get("free_perks", []))
            for enum in unit.ast.enums:
                if not enum.type_params:
                    continue
                exported.append((enum, source))
                record = serialize_generic_enum(enum, source)
                generic_enums.append(record)
                referenced_perks.update(record.get("free_perks", []))

        # Walk the export closure: collect transitive private dependencies
        # (shipping them below) and reject un-shippable references (CE5006).
        closure = self._compute_export_closure(units, exported)

        for fn, src in closure["private_generic_functions"]:
            record = serialize_generic_function(fn, src)
            record["private"] = True
            generic_functions.append(record)
            referenced_perks.update(record.get("free_perks", []))

        private_functions = [
            {
                "name": fn.name,
                "params": [
                    # The mode travels with a private helper too: the consumer calls it
                    # from a monomorphized template body, so it needs the same answer to
                    # "who frees this argument?" the public records carry.
                    {"name": p.name, "type": self._type_to_string(p.ty),
                     "mode": param_mode(p).value}
                    for p in fn.params
                ],
                "return_type": self._type_to_string(fn.ret),
            }
            for fn, _src in closure["private_functions"]
        ]
        shipped_constants = [
            {"name": c.name, "source": slice_decl_source(c, src)}
            for c, src in closure["constants"]
        ]
        closure_summary = {
            "private_functions": sorted(r["name"] for r in private_functions),
            "private_generic_functions": sorted(
                fn.name for fn, _ in closure["private_generic_functions"]
            ),
            "constants": sorted(r["name"] for r in shipped_constants),
        }

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

        from sushi_lang.semantics.passes.collect.perks import _get_type_name
        from sushi_lang.semantics.generics.types import GenericTypeRef

        perk_impls: list[dict] = []
        seen_impls: set[tuple[str, str]] = set()
        for unit in units:
            if unit.ast is None:
                continue
            source = unit.file_path.read_text()
            for impl in unit.ast.perk_impls:
                if impl.perk_name not in seen_perks:
                    continue
                if isinstance(impl.target_type, GenericTypeRef):
                    continue
                type_name = _get_type_name(impl.target_type)
                if type_name is None or (type_name, impl.perk_name) in seen_impls:
                    continue
                if any(
                    self._contains_foreign_ptr(m.ret)
                    or any(self._contains_foreign_ptr(p.ty) for p in m.params)
                    for m in impl.methods
                ):
                    continue
                seen_impls.add((type_name, impl.perk_name))
                perk_impls.append(serialize_perk_impl(impl, source))

        return {
            "version": 4,
            "generic_functions": generic_functions,
            "generic_structs": generic_structs,
            "generic_enums": generic_enums,
            "perks": perks,
            "perk_impls": perk_impls,
            "private_functions": private_functions,
            "constants": shipped_constants,
            "closure_summary": closure_summary,
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
