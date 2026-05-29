"""Unit tests for the incremental-compilation cache (compiler/cache.py).

These cover the manifest-validity rules (compiler version / target triple /
opt level) and the per-unit object staleness contract, all against a temporary
project root. No AST is required.
"""
from __future__ import annotations

import pytest

from sushi_lang.compiler.cache import CacheManager


@pytest.fixture
def cache(tmp_path):
    """A CacheManager rooted at a temp project dir (cache at <root>/__sushi_cache__)."""
    return CacheManager(tmp_path, opt_level="mem2reg")


# --------------------------------------------------------------------------
# Manifest validity
# --------------------------------------------------------------------------

def test_is_valid_false_on_empty_cache(cache):
    assert cache.is_valid() is False


def test_is_valid_true_after_write_manifest(cache):
    cache.write_manifest()
    assert cache.is_valid() is True


def test_is_valid_false_on_opt_level_mismatch(tmp_path):
    CacheManager(tmp_path, opt_level="mem2reg").write_manifest()
    # A second manager over the same cache dir but a different opt level.
    other = CacheManager(tmp_path, opt_level="O2")
    assert other.is_valid() is False


def test_is_valid_false_on_compiler_version_mismatch(cache, monkeypatch):
    cache.write_manifest()  # manifest records the real current version
    monkeypatch.setattr("sushi_lang.compiler.cache.compiler_version", "0.0.0-test")
    # Re-read from disk (drop the in-memory manifest cached by write_manifest).
    cache._manifest = None
    assert cache.is_valid() is False


def test_is_valid_false_on_target_triple_mismatch(cache):
    cache.write_manifest()  # manifest records the real default triple
    cache._target_triple = "totally-not-a-real-triple"
    assert cache.is_valid() is False


def test_invalidate_and_rebuild_leaves_valid_manifest(cache):
    cache.invalidate_and_rebuild()
    assert cache.is_valid() is True
    assert cache.cache_path.exists()


def test_wipe_removes_cache_directory(cache):
    cache.write_manifest()
    assert cache.cache_path.exists()
    cache.wipe()
    assert not cache.cache_path.exists()
    assert cache.is_valid() is False


# --------------------------------------------------------------------------
# Per-unit object staleness contract
# --------------------------------------------------------------------------

def test_store_then_has_cached_unit_with_matching_fingerprint(cache):
    cache.store_unit_object("main", b"OBJ", "fp-abc")
    assert cache.has_cached_unit("main", "fp-abc") is True


def test_has_cached_unit_false_with_different_fingerprint(cache):
    # The core staleness contract: a stored object is NOT reused when the
    # fingerprint differs. This must fail if has_cached_unit stops comparing
    # fingerprints.
    cache.store_unit_object("main", b"OBJ", "fp-abc")
    assert cache.has_cached_unit("main", "fp-different") is False


def test_has_cached_unit_false_when_object_missing(cache):
    assert cache.has_cached_unit("never-stored", "fp-abc") is False


def test_store_unit_object_writes_object_and_fingerprint_sidecar(cache):
    obj_path = cache.store_unit_object("main", b"OBJBYTES", "fp-xyz")
    assert obj_path.exists()
    assert obj_path.read_bytes() == b"OBJBYTES"
    sidecar = obj_path.with_suffix(".fingerprint")
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8").strip() == "fp-xyz"


def test_nested_unit_name_round_trips(cache):
    cache.store_unit_object("helpers/math", b"OBJ", "fp-1")
    assert cache.has_cached_unit("helpers/math", "fp-1") is True
    assert cache.unit_object_path("helpers/math").name == "math.o"


# --------------------------------------------------------------------------
# Path mangling and object collection
# --------------------------------------------------------------------------

def test_stdlib_object_path_mangles_separators(cache):
    path = cache.stdlib_object_path("io/stdio")
    assert path.name == "io_stdio.o"
    assert path.parent == cache.stdlib_path


def test_collect_all_object_paths_includes_units_stdlib_and_libs(cache):
    cache.store_unit_object("main", b"U", "fp-u")
    cache.store_stdlib_object("io/stdio", b"S", "fp-s")
    cache.store_lib_object("mylib", b"L", "fp-l")

    collected = {p.name for p in cache.collect_all_object_paths()}
    assert collected == {"main.o", "io_stdio.o", "mylib.o"}
