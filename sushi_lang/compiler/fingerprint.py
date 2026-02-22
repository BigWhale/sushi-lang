"""Per-unit semantic fingerprint computation for incremental compilation.

A fingerprint captures everything that affects a unit's codegen output:
- Source file content
- Signatures of all symbols visible to the unit (from dependencies)
- Generic instantiations consumed by the unit
- Struct/enum type definitions visible to the unit
- Extension methods visible to the unit

If the fingerprint matches the cached one, the .o file can be reused.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit, UnitManager
    from sushi_lang.semantics.ast import Program


def compute_unit_fingerprint(unit: Unit, unit_manager: UnitManager | None = None,
                             monomorphized_extensions: list | None = None) -> str:
    """Compute a semantic fingerprint for a compilation unit.

    The fingerprint is a hex SHA-256 digest that changes whenever anything
    affecting this unit's codegen output changes.

    Args:
        unit: The compilation unit to fingerprint.
        unit_manager: The unit manager (for cross-unit symbol visibility).
        monomorphized_extensions: Monomorphized extension methods from semantic analysis.

    Returns:
        Hex string of the fingerprint hash.
    """
    hasher = hashlib.sha256()

    # 1. Source content hash
    if unit.file_path.exists():
        source_bytes = unit.file_path.read_bytes()
        hasher.update(b"SOURCE:")
        hasher.update(source_bytes)

    # 2. Own public symbol signatures (sorted for determinism)
    hasher.update(b"OWN_SYMBOLS:")
    for name in sorted(unit.public_symbols.keys()):
        sym = unit.public_symbols[name]
        hasher.update(f"{sym.symbol_type.value}:{name}".encode())
        # Include function signature details
        defn = sym.definition
        hasher.update(_definition_signature(defn).encode())

    # 3. Dependency symbol signatures (transitive visibility)
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

    # 4. AST structural features that affect codegen
    if unit.ast is not None:
        _hash_ast_structure(hasher, unit.ast)

    # 5. Monomorphized extension methods that this unit might use
    if monomorphized_extensions:
        hasher.update(b"MONO_EXT:")
        ext_sigs = sorted(
            f"{ext.target_type}::{ext.name}" for ext in monomorphized_extensions
        )
        for sig in ext_sigs:
            hasher.update(sig.encode())

    return hasher.hexdigest()


def compute_stdlib_fingerprint(bc_paths: list) -> str:
    """Compute a fingerprint for stdlib bitcode files.

    Simply hashes the bitcode content since stdlib doesn't change
    between compilations (only between compiler versions).
    """
    hasher = hashlib.sha256()
    hasher.update(b"STDLIB:")
    for bc_path in sorted(str(p) for p in bc_paths):
        from pathlib import Path
        path = Path(bc_path)
        if path.exists():
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def compute_lib_fingerprint(slib_path) -> str:
    """Compute a fingerprint for a library .slib file."""
    from pathlib import Path
    hasher = hashlib.sha256()
    hasher.update(b"LIB:")
    path = Path(slib_path)
    if path.exists():
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _definition_signature(defn) -> str:
    """Extract a stable signature string from a function/constant definition."""
    from sushi_lang.semantics.ast import FuncDef, ConstDef

    if isinstance(defn, FuncDef):
        params = ",".join(f"{p.ty}:{p.name}" for p in defn.params)
        ret = str(defn.ret) if defn.ret else "~"
        generic = ""
        if hasattr(defn, 'type_params') and defn.type_params:
            generic = "<" + ",".join(defn.type_params) + ">"
        return f"fn{generic}({params})->{ret}"

    if isinstance(defn, ConstDef):
        return f"const:{defn.ty}={defn.name}"

    return str(type(defn).__name__)


def _hash_ast_structure(hasher: hashlib._Hash, ast: Program) -> None:
    """Hash structural features of an AST that affect codegen."""
    # Struct definitions
    hasher.update(b"STRUCTS:")
    for struct in sorted(ast.structs, key=lambda s: s.name):
        fields = ",".join(f"{f.ty}:{f.name}" for f in struct.fields)
        generic = ""
        if hasattr(struct, 'type_params') and struct.type_params:
            generic = "<" + ",".join(struct.type_params) + ">"
        hasher.update(f"{struct.name}{generic}({fields})".encode())

    # Enum definitions
    hasher.update(b"ENUMS:")
    for enum in sorted(ast.enums, key=lambda e: e.name):
        variants = ",".join(
            f"{v.name}({v.data_type})" if v.has_data else v.name
            for v in enum.variants
        )
        generic = ""
        if hasattr(enum, 'type_params') and enum.type_params:
            generic = "<" + ",".join(enum.type_params) + ">"
        hasher.update(f"{enum.name}{generic}[{variants}]".encode())

    # Extension method signatures
    hasher.update(b"EXTENSIONS:")
    for ext in sorted(ast.extensions, key=lambda e: f"{e.target_type}::{e.name}"):
        params = ",".join(f"{p.ty}:{p.name}" for p in ext.params)
        ret = str(ext.ret) if ext.ret else "~"
        hasher.update(f"{ext.target_type}::{ext.name}({params})->{ret}".encode())

    # Perk implementations
    hasher.update(b"PERK_IMPLS:")
    for perk_impl in ast.perk_impls:
        for method in sorted(perk_impl.methods, key=lambda m: m.name):
            params = ",".join(f"{p.ty}:{p.name}" for p in method.params)
            ret = str(method.ret) if method.ret else "~"
            hasher.update(f"{perk_impl.target_type}::{method.name}({params})->{ret}".encode())

    # Use statements (affect stdlib/library linking)
    hasher.update(b"USES:")
    for use_stmt in sorted(ast.uses, key=lambda u: u.path):
        hasher.update(f"{use_stmt.path}:{use_stmt.is_stdlib}:{use_stmt.is_library}".encode())
