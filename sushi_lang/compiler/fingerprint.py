"""Per-unit semantic fingerprint computation for incremental compilation."""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit, UnitManager
    from sushi_lang.semantics.ast import Program


def compute_unit_fingerprint(unit: Unit, unit_manager: UnitManager | None = None,
                             monomorphized_extensions: list | None = None,
                             library_fingerprints: dict[str, str] | None = None,
                             drop_types: frozenset[str] | None = None) -> str:
    """Compute a semantic fingerprint for a compilation unit."""
    hasher = hashlib.sha256()

    if unit.file_path.exists():
        source_bytes = unit.file_path.read_bytes()
        hasher.update(b"SOURCE:")
        hasher.update(source_bytes)

    hasher.update(b"OWN_SYMBOLS:")
    for name in sorted(unit.public_symbols.keys()):
        sym = unit.public_symbols[name]
        hasher.update(f"{sym.symbol_type.value}:{name}".encode())
        defn = sym.definition
        hasher.update(_definition_signature(defn).encode())

    # 4. Every unit this one can read a declaration out of, and the full INTERFACE of
    # each -- not the public function and constant signatures alone (#593). A dependent
    # bakes a struct's LAYOUT, an enum's variant TAGS and a constant's VALUE into its own
    # object, and none of the three was in this digest: a field reorder next door left
    # the dependent reading the other slot, with no diagnostic and no rebuild.
    #
    # The edge set is the dependency GRAPH's, transitively. `Unit.dependencies` holds
    # user-unit imports alone, so a source library's injected units and a bundled
    # Sushi-source stdlib module were outside it entirely; and a `public use` re-exports,
    # so a unit names a type that is two or more units away from its own import.
    if unit_manager is not None:
        hasher.update(b"DEP_SYMBOLS:")
        for dep_name in sorted(_dependency_closure(unit, unit_manager)):
            dep = unit_manager.units.get(dep_name)
            if dep is None:
                continue
            hasher.update(f"DEP:{dep_name}:".encode())
            hasher.update(_interface_digest(dep).encode())

    if unit.ast is not None:
        _hash_ast_structure(hasher, unit.ast)

    # 5. Monomorphized extension methods that this unit might use. The key must
    # cover the full signature AND the body: these are concrete instances whose
    # generic source may live in another unit or a library, so this unit's own
    # source hash does not cover an edit to them. Hashing only target::name (the
    # old key) reused a stale .o across a body or signature change.
    if monomorphized_extensions:
        hasher.update(b"MONO_EXT:")
        ext_sigs = sorted(
            "{}::{}({})->{}|{}".format(
                ext.target_type,
                ext.name,
                ",".join(f"{p.ty}:{p.name}" for p in ext.params),
                str(ext.ret) if ext.ret else "~",
                _node_digest(ext.body),
            )
            for ext in monomorphized_extensions
        )
        for sig in ext_sigs:
            hasher.update(sig.encode())

    # 6. Imported library fingerprints (cross-library generic templates).
    # Folding the whole-`.slib` digest in covers any library template a
    # consumer unit may monomorphize; since the digest hashes the entire file,
    # any template-body change flips it and forces a consumer rebuild.
    if library_fingerprints:
        hasher.update(b"LIB_TEMPLATES:")
        for lib_path in sorted(library_fingerprints):
            hasher.update(f"{lib_path}:{library_fingerprints[lib_path]}".encode())

    # 7. Which types implement `Drop`, whole-program (HANDLES.md ruling R2). Nothing
    # else here covers it: a perk implementation carries no visibility marker, so it is
    # not a public symbol and neither `OWN_SYMBOLS` nor `DEP_SYMBOLS` sees it. Adding
    # `extend File with Drop` changes the OWNERSHIP CLASS of `File` for every unit in
    # the program -- a plain copy becomes a move that closes a descriptor -- while no
    # consumer's own source moves. Without this block the consumer keeps a `.o` built
    # while the type was plain, and the handle is copied and never closed.
    hasher.update(b"DROP_TYPES:")
    for name in sorted(drop_types or ()):
        hasher.update(name.encode())

    return hasher.hexdigest()


def compute_stdlib_fingerprint(bc_paths: list) -> str:
    """Compute a fingerprint for stdlib bitcode files."""
    hasher = hashlib.sha256()
    hasher.update(b"STDLIB:")
    for bc_path in sorted(str(p) for p in bc_paths):
        from pathlib import Path
        path = Path(bc_path)
        if path.exists():
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def compute_stdlib_source_fingerprint() -> str:
    """Compute a content fingerprint of the stdlib bitcode *generators*."""
    sushi_lang_dir = _sushi_lang_dir()
    hasher = hashlib.sha256()
    hasher.update(b"STDLIB_SRC:")
    for path in _stdlib_generator_sources():
        # A listed source that does not exist would be SILENTLY absent from the
        # digest -- exactly how the primitives generator dropped out when it
        # became a package. tests/unit/test_fingerprint.py pins the list.
        if not path.exists():
            continue
        rel = path.resolve().relative_to(sushi_lang_dir)
        hasher.update(f"{rel}:".encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _sushi_lang_dir():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent


def _stdlib_generator_sources() -> list:
    """The generator sources the stdlib fingerprint hashes, sorted by path."""
    sushi_lang_dir = _sushi_lang_dir()
    sources = list((sushi_lang_dir / "sushi_stdlib" / "src").rglob("*.py"))
    sources.extend((sushi_lang_dir / "backend" / "types" / "primitives").rglob("*.py"))
    sources.append(sushi_lang_dir / "sushi_stdlib" / "build.py")
    # The ABI files: the enum layout lives in mapping.py/sizing.py, and the stdlib .bc
    # must byte-match it. Before these were hashed, a backend-only layout change linked
    # a stale .bc SILENTLY (found while landing #300 phase 2).
    sources.append(sushi_lang_dir / "backend" / "types" / "core" / "mapping.py")
    sources.append(sushi_lang_dir / "backend" / "types" / "core" / "sizing.py")
    # The errno tables live here and are baked into the .bc as a chain of selects,
    # so an edit to one has to invalidate the bitcode that carries it.
    sources.append(sushi_lang_dir / "backend" / "runtime" / "constants.py")
    return sorted(sources, key=str)


def compute_lib_fingerprint(slib_path) -> str:
    """Compute a fingerprint for a library .slib file."""
    from pathlib import Path
    hasher = hashlib.sha256()
    hasher.update(b"LIB:")
    path = Path(slib_path)
    if path.exists():
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


_compiler_source_fingerprint: str | None = None


def compute_compiler_source_fingerprint() -> str:
    """Content digest of the compiler's own Python sources."""
    global _compiler_source_fingerprint
    if _compiler_source_fingerprint is not None:
        return _compiler_source_fingerprint

    from pathlib import Path

    sushi_lang_dir = Path(__file__).resolve().parent.parent
    hasher = hashlib.sha256()
    hasher.update(b"COMPILER_SRC:")
    for path in sorted(sushi_lang_dir.rglob("*.py"), key=str):
        rel = path.resolve().relative_to(sushi_lang_dir)
        hasher.update(f"{rel}:".encode())
        hasher.update(path.read_bytes())
    _compiler_source_fingerprint = hasher.hexdigest()
    return _compiler_source_fingerprint


def _node_digest(node) -> str:
    """Stable digest of an AST subtree, insensitive to source positions."""
    import dataclasses

    hasher = hashlib.sha256()
    seen: set[int] = set()

    def walk(value) -> None:
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            # Recursive generics tie the knot with self-referential type objects
            # (e.g. a monomorphized Own<Tree<i32>> struct); stamped annotations on
            # body nodes can reach them, so guard against cycles.
            if id(value) in seen:
                hasher.update(b"<cycle>")
                return
            seen.add(id(value))
            hasher.update(type(value).__name__.encode())
            for f in dataclasses.fields(value):
                if f.name == "loc" or f.name.endswith("_span"):
                    continue
                hasher.update(f"|{f.name}=".encode())
                walk(getattr(value, f.name))
        elif isinstance(value, (list, tuple)):
            hasher.update(b"[")
            for item in value:
                walk(item)
                hasher.update(b",")
            hasher.update(b"]")
        elif isinstance(value, dict):
            hasher.update(b"{")
            for k in sorted(value, key=str):
                hasher.update(f"{k}:".encode())
                walk(value[k])
            hasher.update(b"}")
        else:
            hasher.update(repr(value).encode())

    walk(node)
    return hasher.hexdigest()[:16]


def _type_params_signature(node) -> str:
    """A declaration's `@(...)` list, or the empty string.

    type_params are BoundedTypeParam objects; str() renders the name, the constraints
    and the pack marker. Joining them raw was a TypeError, so every incremental build
    exporting a generic declaration ICEd (CE0000).
    """
    params = getattr(node, "type_params", None)
    if not params:
        return ""
    return "<" + ",".join(str(tp) for tp in params) + ">"


def _param_signature(param) -> str:
    """One parameter, with the mode bits its TYPE cannot carry.

    `nom` says the CALLEE frees, and it rides on the Param rather than on the type
    (borrow-model.md S6), so a signature built from `p.ty` alone reads a borrow and an
    ownership transfer as the same thing.
    """
    marks = ""
    if getattr(param, "is_nom", False):
        marks += "nom "
    if getattr(param, "is_variadic", False):
        marks += "..."
    if getattr(param, "is_pack", False):
        marks += "...pack "
    return f"{marks}{param.ty}:{param.name}"


def _params_signature(params) -> str:
    return ",".join(_param_signature(p) for p in params)


def _callable_signature(defn) -> str:
    """A function or a perk-implementation method: what a CALL SITE has to agree with."""
    ret = str(defn.ret) if defn.ret else "~"
    err = f"|{defn.err_type}" if getattr(defn, "err_type", None) else ""
    recv = f"{defn.self_mode} self;" if getattr(defn, "self_mode", None) else ""
    return (f"fn{_type_params_signature(defn)}"
            f"({recv}{_params_signature(defn.params)})->{ret}{err}")


def _struct_signature(struct) -> str:
    """The LAYOUT: the field order and each field's type."""
    fields = ",".join(f"{f.ty}:{f.name}" for f in struct.fields)
    return f"{struct.name}{_type_params_signature(struct)}({fields})"


def _enum_signature(enum) -> str:
    """The variant ORDER (a variant's tag is its position) and each payload."""
    variants = ",".join(
        f"{v.name}({','.join(str(t) for t in v.associated_types)})"
        if v.associated_types else v.name
        for v in enum.variants
    )
    return f"{enum.name}{_type_params_signature(enum)}[{variants}]"


def _perk_signature(perk) -> str:
    methods = ",".join(
        f"{m.name}({_params_signature(m.params)})->{str(m.ret) if m.ret else '~'}"
        for m in perk.methods
    )
    return f"{perk.name}{_type_params_signature(perk)}{{{methods}}}"


def _extension_signature(ext) -> str:
    """An extension method as a caller sees it: receiver, parameters, return, channel."""
    ret = str(ext.ret) if ext.ret else "~"
    err = f"|{ext.err_type}" if getattr(ext, "err_type", None) else ""
    recv = "static" if getattr(ext, "is_static", False) else (ext.self_mode or "self")
    return (f"{ext.target_type}::{ext.name}{_type_params_signature(ext)}"
            f"({recv};{_params_signature(ext.params)})->{ret}{err}")


def _perk_impl_signatures(impl) -> list[str]:
    return [f"{impl.target_type}:{impl.perk_name}::{m.name}{_callable_signature(m)}"
            for m in impl.methods]


def _definition_signature(defn) -> str:
    """A stable signature string for any declaration a unit can export.

    Total by construction: a kind with no case here falls through to a digest of the
    NODE, never to its class name. The class name was a constant, so a declaration whose
    shape changed produced an unchanged signature and a stale object file (#593).
    """
    from sushi_lang.semantics.ast import (
        ConstDef, EnumDef, ExtendDef, ExtendWithDef, FuncDef, PerkDef, StructDef, VarDef,
    )

    if isinstance(defn, FuncDef):
        return _callable_signature(defn)

    if isinstance(defn, VarDef):
        # Unit-level STORAGE, not a folded value: the address is the interface, and a
        # binary library's consumer declares it under `link_symbol`.
        link = f"@{defn.link_symbol}" if defn.link_symbol else ""
        return f"var:{defn.ty}:{defn.name}={_node_digest(defn.value)}{link}"

    if isinstance(defn, ConstDef):
        # The VALUE, because a constant is folded into every unit that names it. The old
        # signature carried the type and the name only, so `LIMIT = 10` -> `LIMIT = 20`
        # left every dependent printing 10 out of a cached object.
        return f"const:{defn.ty}:{defn.name}={_node_digest(defn.value)}"

    if isinstance(defn, StructDef):
        return f"struct:{_struct_signature(defn)}"

    if isinstance(defn, EnumDef):
        return f"enum:{_enum_signature(defn)}"

    if isinstance(defn, PerkDef):
        return f"perk:{_perk_signature(defn)}"

    if isinstance(defn, ExtendDef):
        return f"extend:{_extension_signature(defn)}"

    if isinstance(defn, ExtendWithDef):
        return "impl:" + ";".join(_perk_impl_signatures(defn))

    return f"{type(defn).__name__}:{_node_digest(defn)}"


def _dependency_closure(unit, unit_manager) -> set:
    """Every unit whose declarations `unit` can read, however many hops away.

    The graph rather than `Unit.dependencies`: a source library's units and a bundled
    Sushi-source stdlib module are injected after the units are loaded, so the import
    that reaches them spells no user path and stores no edge (`units.py`, section 13.2).
    Transitive because `public use` RE-EXPORTS -- a unit names a type its own import
    does not declare, and the first ring of the graph does not hold that type.
    """
    graph = unit_manager.build_dependency_graph()
    closure: set = set()
    queue = list(graph.get(unit.name, unit.dependencies))
    while queue:
        name = queue.pop()
        if name == unit.name or name in closure:
            continue
        closure.add(name)
        queue.extend(graph.get(name, ()))
    return closure


def _interface_digest(unit) -> str:
    """What a DEPENDENT of `unit` can bake into its own object file.

    The public symbols plus every declaration shape. Not the bodies: a private body is
    the declaring unit's own business and lives in the declaring unit's object, and a
    generic template a dependent monomorphizes is covered by MONO_EXT and by the
    library digests instead.
    """
    hasher = hashlib.sha256()
    for name in sorted(unit.public_symbols.keys()):
        sym = unit.public_symbols[name]
        hasher.update(f"{sym.symbol_type.value}:{name}".encode())
        hasher.update(_definition_signature(sym.definition).encode())
    if unit.ast is not None:
        _hash_declaration_shapes(hasher, unit.ast)
    return hasher.hexdigest()


def _hash_declaration_shapes(hasher: hashlib._Hash, ast: Program) -> None:
    """Hash every declaration SHAPE in an AST.

    One writer, two readers: a unit's own fingerprint and the interface digest a
    dependent folds in. The two used to disagree -- a dependent saw a struct's name and
    nothing about its fields -- and that disagreement is #593.
    """
    hasher.update(b"STRUCTS:")
    for struct in sorted(ast.structs, key=lambda s: s.name):
        hasher.update(_struct_signature(struct).encode())

    hasher.update(b"ENUMS:")
    for enum in sorted(ast.enums, key=lambda e: e.name):
        hasher.update(_enum_signature(enum).encode())

    hasher.update(b"PERKS:")
    for perk in sorted(ast.perks, key=lambda p: p.name):
        hasher.update(_perk_signature(perk).encode())

    # Both lists: the collector moves a GENERIC target's extension to
    # `generic_extensions`, and a dependent monomorphizes that template. Sorted on the
    # WHOLE signature -- two methods can share a target and a name (a template beside
    # its monomorphized copies), so the name alone is not a stable key.
    hasher.update(b"EXTENSIONS:")
    all_extensions = list(ast.extensions) + list(ast.generic_extensions or ())
    for sig in sorted(_extension_signature(ext) for ext in all_extensions):
        hasher.update(sig.encode())

    # The monomorphized copies of a GENERIC-target implementation are appended to the
    # declaring unit's list before this runs, so a new instantiation anywhere in the
    # program changes that unit's signature list and flips its key. The BODY needs no
    # digest here for the same reason: it comes from the template, whose source is in
    # the declaring unit's own source hash.
    hasher.update(b"PERK_IMPLS:")
    all_impls = list(ast.perk_impls) + list(ast.generic_perk_impls or ())
    impl_sigs = [s for impl in all_impls for s in _perk_impl_signatures(impl)]
    for sig in sorted(impl_sigs):
        hasher.update(sig.encode())


def _hash_ast_structure(hasher: hashlib._Hash, ast: Program) -> None:
    """Hash structural features of an AST that affect codegen."""
    _hash_declaration_shapes(hasher, ast)

    # Monomorphized instances appended to this unit. The unit's own source hash cannot
    # cover them: the generic may be declared here while the INSTANTIATION comes from
    # anywhere in the program, so two builds of the same source can legitimately need
    # different objects. Signature and body both, for the same reason MONO_EXT hashes
    # both -- an edit to the generic's body must flip the key.
    hasher.update(b"SYNTHESIZED:")
    for fn in sorted((f for f in ast.functions if getattr(f, "is_synthesized", False)),
                     key=lambda f: f.name):
        params = ",".join(f"{p.ty}:{p.name}" for p in fn.params)
        ret = str(fn.ret) if fn.ret else "~"
        hasher.update(f"{fn.name}({params})->{ret}|{_node_digest(fn.body)}".encode())

    hasher.update(b"USES:")
    for use_stmt in sorted(ast.uses, key=lambda u: u.path):
        hasher.update(f"{use_stmt.path}:{use_stmt.is_stdlib}:{use_stmt.is_library}".encode())
