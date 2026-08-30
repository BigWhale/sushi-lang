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

    if unit_manager is not None:
        hasher.update(b"DEP_SYMBOLS:")
        for dep_name in sorted(unit.dependencies):
            dep = unit_manager.units.get(dep_name)
            if dep is None:
                continue
            hasher.update(f"DEP:{dep_name}:".encode())
            for sym_name in sorted(dep.public_symbols.keys()):
                sym = dep.public_symbols[sym_name]
                hasher.update(f"{sym.symbol_type.value}:{sym_name}".encode())
                hasher.update(_definition_signature(sym.definition).encode())

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


def _definition_signature(defn) -> str:
    """Extract a stable signature string from a function/constant definition."""
    from sushi_lang.semantics.ast import FuncDef, ConstDef

    if isinstance(defn, FuncDef):
        params = ",".join(f"{p.ty}:{p.name}" for p in defn.params)
        ret = str(defn.ret) if defn.ret else "~"
        generic = ""
        if hasattr(defn, 'type_params') and defn.type_params:
            # type_params are BoundedTypeParam objects; str() renders the name,
            # constraints and pack marker. Joining them raw was a TypeError, so
            # every incremental build exporting a generic fn ICEd (CE0000).
            generic = "<" + ",".join(str(tp) for tp in defn.type_params) + ">"
        return f"fn{generic}({params})->{ret}"

    if isinstance(defn, ConstDef):
        return f"const:{defn.ty}={defn.name}"

    return str(type(defn).__name__)


def _hash_ast_structure(hasher: hashlib._Hash, ast: Program) -> None:
    """Hash structural features of an AST that affect codegen."""
    hasher.update(b"STRUCTS:")
    for struct in sorted(ast.structs, key=lambda s: s.name):
        fields = ",".join(f"{f.ty}:{f.name}" for f in struct.fields)
        generic = ""
        if hasattr(struct, 'type_params') and struct.type_params:
            generic = "<" + ",".join(str(tp) for tp in struct.type_params) + ">"
        hasher.update(f"{struct.name}{generic}({fields})".encode())

    hasher.update(b"ENUMS:")
    for enum in sorted(ast.enums, key=lambda e: e.name):
        variants = ",".join(
            f"{v.name}({','.join(str(t) for t in v.associated_types)})" if v.associated_types else v.name
            for v in enum.variants
        )
        generic = ""
        if hasattr(enum, 'type_params') and enum.type_params:
            generic = "<" + ",".join(str(tp) for tp in enum.type_params) + ">"
        hasher.update(f"{enum.name}{generic}[{variants}]".encode())

    hasher.update(b"EXTENSIONS:")
    for ext in sorted(ast.extensions, key=lambda e: f"{e.target_type}::{e.name}"):
        params = ",".join(f"{p.ty}:{p.name}" for p in ext.params)
        ret = str(ext.ret) if ext.ret else "~"
        hasher.update(f"{ext.target_type}::{ext.name}({params})->{ret}".encode())

    hasher.update(b"PERK_IMPLS:")
    for perk_impl in ast.perk_impls:
        for method in sorted(perk_impl.methods, key=lambda m: m.name):
            params = ",".join(f"{p.ty}:{p.name}" for p in method.params)
            ret = str(method.ret) if method.ret else "~"
            hasher.update(f"{perk_impl.target_type}::{method.name}({params})->{ret}".encode())

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
