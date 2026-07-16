"""Unit tests for semantic fingerprinting (compiler/fingerprint.py).

Fingerprints decide cache reuse, so the contract is: identical inputs produce
identical fingerprints, and anything that affects codegen changes them.

Note on isolation: compute_unit_fingerprint() always hashes the unit's source
bytes, so any source edit flips the fingerprint regardless of the AST-structure
hashing. To verify the _hash_ast_structure branches specifically, the AST tests
below hold the on-disk file bytes constant and vary only the parsed AST.
"""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.units import Unit, UnitManager
from sushi_lang.compiler.fingerprint import (
    compute_unit_fingerprint,
    compute_stdlib_fingerprint,
    compute_lib_fingerprint,
)


CLEAN = "fn main() i32:\n    return Result.Ok(0)\n"

# Constant file bytes shared by the AST-isolation tests: the file content is
# identical across variants, so any fingerprint difference comes from the AST.
_FIXED_BYTES = "# fixed source bytes for AST-isolation tests\n"


def _unit_with_ast(tmp_path, src, name="main"):
    """Build a Unit whose file bytes are constant but whose AST comes from `src`."""
    fp = tmp_path / f"{name}.sushi"
    fp.write_text(_FIXED_BYTES, encoding="utf-8")
    text = src if src.endswith("\n") else src + "\n"
    program, _tree = parse_to_ast(text)
    return Unit(name=name, file_path=fp, ast=program, dependencies=[], public_symbols={})


# --------------------------------------------------------------------------
# Determinism and source sensitivity
# --------------------------------------------------------------------------

def test_fingerprint_is_deterministic(make_unit):
    unit = make_unit(CLEAN)
    assert compute_unit_fingerprint(unit) == compute_unit_fingerprint(unit)


def test_fingerprint_changes_with_source(make_unit):
    # Distinct names so the two units are backed by distinct files (the source
    # bytes are what distinguish function-body changes; _hash_ast_structure does
    # not hash function bodies).
    a = make_unit("fn main() i32:\n    return Result.Ok(0)\n", name="a")
    b = make_unit("fn main() i32:\n    return Result.Ok(1)\n", name="b")
    assert compute_unit_fingerprint(a) != compute_unit_fingerprint(b)


# --------------------------------------------------------------------------
# AST-structure sensitivity (file bytes held constant, only AST varies)
# --------------------------------------------------------------------------

def test_fingerprint_changes_with_struct_field(tmp_path):
    a = _unit_with_ast(tmp_path, "struct P:\n    i32 x\n" + CLEAN)
    b = _unit_with_ast(tmp_path, "struct P:\n    i32 x\n    i32 y\n" + CLEAN)
    assert a.file_path.read_bytes() == b.file_path.read_bytes()  # same source bytes
    assert compute_unit_fingerprint(a) != compute_unit_fingerprint(b)


def test_fingerprint_changes_with_enum_variant(tmp_path):
    a = _unit_with_ast(tmp_path, "enum Status:\n    Active()\n    Inactive()\n" + CLEAN)
    b = _unit_with_ast(tmp_path, "enum Status:\n    Active()\n    Inactive()\n    Pending()\n" + CLEAN)
    assert compute_unit_fingerprint(a) != compute_unit_fingerprint(b)


def test_fingerprint_changes_with_extension_method(tmp_path):
    a = _unit_with_ast(tmp_path, CLEAN)
    b = _unit_with_ast(tmp_path, "extend i32 squared() i32:\n    return Result.Ok(self * self)\n" + CLEAN)
    assert compute_unit_fingerprint(a) != compute_unit_fingerprint(b)


def test_fingerprint_changes_with_use_statement(tmp_path):
    # Guards the USES: component of _hash_ast_structure specifically.
    a = _unit_with_ast(tmp_path, CLEAN)
    b = _unit_with_ast(tmp_path, "use <io/stdio>\n" + CLEAN)
    assert compute_unit_fingerprint(a) != compute_unit_fingerprint(b)


# --------------------------------------------------------------------------
# Cross-unit visibility (a dependency's public signature affects dependents)
# --------------------------------------------------------------------------

def test_fingerprint_changes_when_dependency_signature_changes(make_unit, tmp_path):
    dependent = make_unit(CLEAN, name="dependent")
    dependent.dependencies = ["dep"]

    dep_v1 = make_unit("public fn helper(i32 a) i32:\n    return Result.Ok(a)\n", name="dep")
    um = UnitManager(root_path=tmp_path)
    um.units = {"dep": dep_v1, "dependent": dependent}
    fp1 = compute_unit_fingerprint(dependent, um)

    # Dependent's own source is unchanged; only the dependency's public signature is.
    dep_v2 = make_unit("public fn helper(i32 a, i32 b) i32:\n    return Result.Ok(a)\n", name="dep")
    um.units["dep"] = dep_v2
    fp2 = compute_unit_fingerprint(dependent, um)

    assert fp1 != fp2


# --------------------------------------------------------------------------
# stdlib / library fingerprints
# --------------------------------------------------------------------------

def test_stdlib_fingerprint_is_order_independent(tmp_path):
    a = tmp_path / "a.bc"; a.write_bytes(b"AAA")
    b = tmp_path / "b.bc"; b.write_bytes(b"BBB")
    assert compute_stdlib_fingerprint([a, b]) == compute_stdlib_fingerprint([b, a])


def test_stdlib_fingerprint_changes_with_content(tmp_path):
    a = tmp_path / "a.bc"; a.write_bytes(b"AAA")
    b = tmp_path / "b.bc"; b.write_bytes(b"BBB")
    before = compute_stdlib_fingerprint([a, b])
    b.write_bytes(b"CHANGED")
    assert compute_stdlib_fingerprint([a, b]) != before


def test_lib_fingerprint_changes_with_content(tmp_path):
    slib = tmp_path / "lib.slib"
    slib.write_bytes(b"LIB1")
    fp1 = compute_lib_fingerprint(slib)
    slib.write_bytes(b"LIB2")
    assert compute_lib_fingerprint(slib) != fp1


# --------------------------------------------------------------------------
# Imported-library template sensitivity (Phase 2 cross-library generics)
# --------------------------------------------------------------------------

def test_unit_fingerprint_changes_with_imported_library_digest(make_unit):
    """A consumer unit's fingerprint must change when an imported library's
    digest changes, so a library generic-template edit invalidates the
    consumer's cached .o (otherwise a monomorphized instance would be stale)."""
    unit = make_unit(CLEAN)
    fp_a = compute_unit_fingerprint(unit, library_fingerprints={"lib/math": "DIGEST_A"})
    fp_b = compute_unit_fingerprint(unit, library_fingerprints={"lib/math": "DIGEST_B"})
    assert fp_a != fp_b


def test_unit_fingerprint_stable_for_same_library_digest(make_unit):
    """Identical library digests must produce identical fingerprints (no
    spurious cache misses)."""
    unit = make_unit(CLEAN)
    digests = {"lib/math": "DIGEST_A", "lib/strings": "DIGEST_X"}
    assert (
        compute_unit_fingerprint(unit, library_fingerprints=dict(digests))
        == compute_unit_fingerprint(unit, library_fingerprints=dict(digests))
    )


def test_unit_fingerprint_no_library_matches_empty_mapping(make_unit):
    """Passing no library digests and an empty mapping are equivalent (the
    section is skipped when there is nothing to fold in)."""
    unit = make_unit(CLEAN)
    assert (
        compute_unit_fingerprint(unit)
        == compute_unit_fingerprint(unit, library_fingerprints={})
    )


# --------------------------------------------------------------------------
# Generic exporters (regression: BoundedTypeParam join TypeError)
# --------------------------------------------------------------------------

def test_definition_signature_with_generic_type_params():
    """A FuncDef whose type_params are BoundedTypeParam objects must produce a
    signature, not a TypeError. Before the fix, ",".join(defn.type_params) made
    every incremental build exporting a generic function a CE0000 ICE."""
    from sushi_lang.compiler.fingerprint import _definition_signature
    from sushi_lang.semantics.ast import BoundedTypeParam
    program, _ = parse_to_ast("public fn identity<T>(T x) T:\n    return Result.Ok(x)\n")
    sig = _definition_signature(program.functions[0])
    assert "<T>" in sig

    # A constraint is part of the signature (a constraint change must invalidate).
    program2, _ = parse_to_ast(
        "perk Hashable:\n    fn hash() u64\n\n"
        "public fn identity<T: Hashable>(T x) T:\n    return Result.Ok(x)\n"
    )
    sig2 = _definition_signature(program2.functions[0])
    assert sig2 != sig
    assert "Hashable" in sig2


def test_fingerprint_generic_struct_and_enum_do_not_crash(tmp_path):
    """Generic struct/enum type_params went through the same broken join."""
    src = (
        "struct Wrap<T>:\n    T inner\n\n"
        "enum Slot<T>:\n    Filled(T)\n    Empty\n" + CLEAN
    )
    unit = _unit_with_ast(tmp_path, src)
    plain = _unit_with_ast(tmp_path, CLEAN, name="plain")
    assert compute_unit_fingerprint(unit) != compute_unit_fingerprint(plain)


# --------------------------------------------------------------------------
# Monomorphized-extension key: signature AND body, span-insensitive
# --------------------------------------------------------------------------

def _parse_extension(src):
    program, _ = parse_to_ast(src)
    return program.extensions[0]


def test_mono_ext_fingerprint_covers_body(make_unit):
    """The old key was target::name only, so a body edit reused a stale .o."""
    unit = make_unit(CLEAN)
    ext_a = _parse_extension("extend i32 squared() i32:\n    return Result.Ok(self * self)\n")
    ext_b = _parse_extension("extend i32 squared() i32:\n    return Result.Ok(self + self)\n")
    fp_a = compute_unit_fingerprint(unit, monomorphized_extensions=[ext_a])
    fp_b = compute_unit_fingerprint(unit, monomorphized_extensions=[ext_b])
    assert fp_a != fp_b


def test_mono_ext_fingerprint_covers_signature(make_unit):
    unit = make_unit(CLEAN)
    ext_a = _parse_extension("extend i32 scaled(i32 k) i32:\n    return Result.Ok(self * k)\n")
    ext_b = _parse_extension("extend i32 scaled(i64 k) i32:\n    return Result.Ok(self)\n")
    assert (
        compute_unit_fingerprint(unit, monomorphized_extensions=[ext_a])
        != compute_unit_fingerprint(unit, monomorphized_extensions=[ext_b])
    )


def test_mono_ext_fingerprint_ignores_source_position(make_unit):
    """Shifting an extension down a line must NOT invalidate (span-insensitive):
    otherwise every unrelated edit above it would rebuild all consumers."""
    unit = make_unit(CLEAN)
    ext_a = _parse_extension("extend i32 squared() i32:\n    return Result.Ok(self * self)\n")
    ext_b = _parse_extension("# shifted\n\n\nextend i32 squared() i32:\n    return Result.Ok(self * self)\n")
    assert (
        compute_unit_fingerprint(unit, monomorphized_extensions=[ext_a])
        == compute_unit_fingerprint(unit, monomorphized_extensions=[ext_b])
    )
