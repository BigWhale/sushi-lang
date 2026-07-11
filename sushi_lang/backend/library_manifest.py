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

    def generate(self, units: list['Unit'], output_path: Path, bitcode: bytes,
                 templates: dict | None = None) -> None:
        """Generate .slib library file.

        Args:
            units: Compilation units in library.
            output_path: Path to .slib file (e.g., mylib.slib).
            bitcode: LLVM bitcode bytes.
            templates: Pre-computed ``_extract_templates`` result. The pipeline
                computes it BEFORE bitcode compilation (the export closure
                decides which private functions need external linkage in the
                bitcode) and passes it here to avoid extracting twice.
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
            "templates": templates if templates is not None else self._extract_templates(units),
            "dependencies": self._extract_dependencies(units),
        }

        # Write .slib binary format
        LibraryFormat.write(output_path, manifest, bitcode)

    def _contains_foreign_ptr(self, ty) -> bool:
        """Recursively check whether a type exposes a foreign `ptr` (ForeignPtrType).

        Delegates to the shared semantic predicate (also used by the CE5008
        unit fence in Pass 2); kept as a method for call-site brevity.
        """
        from sushi_lang.semantics.type_predicates import contains_foreign_ptr
        return contains_foreign_ptr(ty)

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

    def _compute_export_closure(self, units: list['Unit'], exported: list) -> dict:
        """Walk every exported generic and collect the library-private symbols
        its body (transitively) depends on - the EXPORT CLOSURE (C4b/C5).

        Each private dependency ships so the consumer can monomorphize the
        generic without visibility into the library source:

        - **private generic function** -> ships as a source template (the
          consumer monomorphizes it exactly like a public one);
        - **private concrete function** -> ships as a signature record only
          (its definition is already in the library bitcode; the consumer
          declares and links);
        - **constant** -> ships with its source (the consumer needs the VALUE
          for compile-time evaluation; constant globals are emitted with
          internal linkage per module, so re-emission cannot collide);
        - **concrete struct/enum** -> already ships in the manifest type
          sections; only walked for transitive references.

        Only genuinely un-shippable references abort the export with CE5006,
        attributed to the exported generic at the root of the dependency
        chain: an ``unsafe external`` namespace (foreign bindings cannot be
        re-declared at the consumer), a private function whose signature
        exposes a foreign ``ptr`` (CE5002's rationale), and a private v1
        native variadic ``...T`` function (no shippable template - CE0116's
        rationale).

        The walk is a visited-set worklist over a finite symbol table, so
        recursive and mutually-recursive helpers terminate. The scans
        over-collect (locals are not subtracted), which here means at worst
        over-SHIPPING a same-named private symbol - safe, unlike the previous
        scheme where over-collection caused spurious rejections.

        Args:
            units: All library units.
            exported: The exported generic nodes seeding the walk.

        Returns:
            ``{"private_functions": [(FuncDef, source)],
               "private_generic_functions": [(FuncDef, source)],
               "constants": [(ConstDef, source)]}`` in first-seen order.
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
                    # Concrete types already ship; generic types ship as
                    # templates. Walk them for transitive references only.
                    tnode, _src = types_by_name[name]
                    visited.add(name)
                    _walk(tnode, root)
                # Anything else: builtins, params, locals, public symbols -
                # resolvable at the consumer without shipping.

        for node, _source in exported:
            _walk(node, node)

        return {
            "private_functions": list(shipped_fns.values()),
            "private_generic_functions": list(shipped_generic_fns.values()),
            "constants": list(shipped_consts.values()),
        }

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
        - `perk_impls` (C4a): the library's own concrete
          `extend <Type> with <Perk>:` implementations of those shipped perks.
          Their bodies are already compiled into the library bitcode (with weak
          linkage, so a consumer's local impl overrides at link time); the
          record carries signatures (re-parsable source) and symbol names so
          the consumer can register the impl and declare-and-link. Impls of
          unshipped perks stay library-internal; generic-target impls and impls
          whose signatures expose a foreign `ptr` are skipped (the consumer
          falls back to writing its own impl).
        - the EXPORT CLOSURE (C4b/C5): the library-private symbols exported
          generic bodies transitively reference. `private_functions` ships
          signature records (definitions link from the bitcode);
          private generic functions ship into `generic_functions` flagged
          `"private": True`; `constants` ship with their source (values are
          needed for compile-time evaluation). `closure_summary` records what
          shipped, by kind, for observability. See _compute_export_closure.
        """
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

        # Private generic functions ride the existing template path, flagged
        # so the consumer can apply closure (not local-wins) clash semantics.
        for fn, src in closure["private_generic_functions"]:
            record = serialize_generic_function(fn, src)
            record["private"] = True
            generic_functions.append(record)
            referenced_perks.update(record.get("free_perks", []))

        private_functions = [
            {
                "name": fn.name,
                "params": [
                    {"name": p.name, "type": self._type_to_string(p.ty)}
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

        # Ship the library's own concrete impls of the shipped perks (C4a).
        # Signatures + symbol names only - the bodies are in the bitcode.
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
                # Generic-target impls (extend List<T> with ...) are not
                # supported in-program; only concrete targets ship.
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
