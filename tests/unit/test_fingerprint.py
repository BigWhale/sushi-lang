"""Unit tests for semantic fingerprinting (compiler/fingerprint.py)."""
from __future__ import annotations

from sushi_lang.compiler.fingerprint import (
    compute_stdlib_fingerprint,
    compute_lib_fingerprint,
)



# Constant file bytes shared by the AST-isolation tests: the file content is
# identical across variants, so any fingerprint difference comes from the AST.




# Determinism and source sensitivity





# AST-structure sensitivity (file bytes held constant, only AST varies)









# Cross-unit visibility (a dependency's public signature affects dependents)



# stdlib / library fingerprints

def test_stdlib_fingerprint_is_order_independent(tmp_path):
    a = tmp_path / "a.bc"
    a.write_bytes(b"AAA")
    b = tmp_path / "b.bc"
    b.write_bytes(b"BBB")
    assert compute_stdlib_fingerprint([a, b]) == compute_stdlib_fingerprint([b, a])


def test_stdlib_fingerprint_changes_with_content(tmp_path):
    a = tmp_path / "a.bc"
    a.write_bytes(b"AAA")
    b = tmp_path / "b.bc"
    b.write_bytes(b"BBB")
    before = compute_stdlib_fingerprint([a, b])
    b.write_bytes(b"CHANGED")
    assert compute_stdlib_fingerprint([a, b]) != before


def test_lib_fingerprint_changes_with_content(tmp_path):
    slib = tmp_path / "lib.slib"
    slib.write_bytes(b"LIB1")
    fp1 = compute_lib_fingerprint(slib)
    slib.write_bytes(b"LIB2")
    assert compute_lib_fingerprint(slib) != fp1


# Imported-library template sensitivity (Phase 2 cross-library generics)







# Generic exporters (regression: BoundedTypeParam join TypeError)





# Monomorphized-extension key: signature AND body, span-insensitive









# Stdlib generator-source coverage (regression: silently-skipped dead path)

def test_stdlib_generator_sources_all_exist():
    """Every path the stdlib source fingerprint hashes must exist. The hasher silently skips
    missing paths, so a generator that moves (types/primitives.py became the types/primitives/
    package) drops out of the digest without a trace - and editing it no longer invalidates the
    shipped .bc.
    """
    from sushi_lang.compiler.fingerprint import _stdlib_generator_sources

    sources = _stdlib_generator_sources()
    missing = [str(p) for p in sources if not p.exists()]
    assert not missing, f"fingerprinted generator sources do not exist: {missing}"


def test_stdlib_generator_sources_cover_primitives_package():
    """build.py generates core/primitives from backend/types/primitives/ (a package); its files
    must be part of the stdlib source digest.
    """
    from sushi_lang.compiler.fingerprint import _stdlib_generator_sources

    sources = _stdlib_generator_sources()
    prim = [p for p in sources if "backend" in p.parts and "primitives" in p.parts]
    assert prim, "no backend/types/primitives/ files in the stdlib source fingerprint"


# A dependency's declaration SHAPE, not just its public signatures (#593)



























# The dependency EDGE: an injected unit and a re-exported one (#593)



