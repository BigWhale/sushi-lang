"""Unit tests for semantic fingerprinting (compiler/fingerprint.py).

Fingerprints decide cache reuse, so the contract is: identical inputs produce
identical fingerprints, and anything that affects codegen changes them.

Note on isolation: compute_unit_fingerprint() always hashes the unit's source
bytes, so any source edit flips the fingerprint regardless of the AST-structure
hashing. To verify the _hash_ast_structure branches specifically, the AST tests
below hold the on-disk file bytes constant and vary only the parsed AST.
"""
from __future__ import annotations

import pytest

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


@pytest.mark.xfail(
    reason="fingerprint.py:_hash_ast_structure reads EnumVariant.has_data/.data_type, "
           "but EnumVariant has associated_types; compute_unit_fingerprint crashes on "
           "any unit containing an enum. Tracked in issue #26 (remove xfail when fixed).",
    raises=AttributeError,
    strict=True,
)
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
