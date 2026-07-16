"""Unit tests for the incremental-compilation cache (compiler/cache.py).

These cover the staleness contract: a cached object is reused only when it was built
from the same unit, by the same compiler, with the same settings. All three live in
the object's *name* (see CacheManager.global_key), so staleness is a miss rather than
an eviction -- which is what makes the cache safe to share between concurrent
compilers (issue #196; the concurrency contract itself is in test_cache_concurrency.py).

No AST is required; everything runs against a temporary project root.
"""
from __future__ import annotations

import pytest

from sushi_lang.compiler.cache import CacheManager


@pytest.fixture
def cache(tmp_path):
    """A CacheManager rooted at a temp project dir (cache at <root>/__sushi_cache__)."""
    cm = CacheManager(tmp_path, opt_level="mem2reg")
    cm.prepare()
    return cm


# --------------------------------------------------------------------------
# Staleness: the global parameters
# --------------------------------------------------------------------------

def test_global_key_differs_on_opt_level(tmp_path):
    a = CacheManager(tmp_path, opt_level="mem2reg")
    b = CacheManager(tmp_path, opt_level="O2")
    assert a.global_key != b.global_key


def test_global_key_differs_on_compiler_version(cache, monkeypatch):
    before = cache.global_key
    monkeypatch.setattr("sushi_lang.compiler.cache.compiler_version", "0.0.0-test")
    assert cache.global_key != before


def test_global_key_differs_on_target_triple(cache):
    before = cache.global_key
    cache._target_triple = "totally-not-a-real-triple"
    assert cache.global_key != before


def test_object_built_with_other_settings_is_not_reused(tmp_path):
    """The contract the manifest used to enforce by wiping: no false hit."""
    CacheManager(tmp_path, opt_level="mem2reg").store_unit_object("main", b"OBJ", "fp-1")

    o2 = CacheManager(tmp_path, opt_level="O2")
    assert o2.has_cached_unit("main", "fp-1") is False


# --------------------------------------------------------------------------
# Staleness: the per-unit fingerprint
# --------------------------------------------------------------------------

def test_store_then_has_cached_unit_with_matching_fingerprint(cache):
    cache.store_unit_object("main", b"OBJ", "fp-abc")
    assert cache.has_cached_unit("main", "fp-abc") is True


def test_has_cached_unit_false_with_different_fingerprint(cache):
    # The core staleness contract: a stored object is NOT reused when the
    # fingerprint differs.
    cache.store_unit_object("main", b"OBJ", "fp-abc")
    assert cache.has_cached_unit("main", "fp-different") is False


def test_has_cached_unit_false_when_object_missing(cache):
    assert cache.has_cached_unit("never-stored", "fp-abc") is False


def test_store_unit_object_round_trips_the_bytes(cache):
    obj_path = cache.store_unit_object("main", b"OBJBYTES", "fp-xyz")
    assert obj_path.exists()
    assert obj_path.read_bytes() == b"OBJBYTES"


def test_nested_unit_name_mirrors_the_source_tree(cache):
    cache.store_unit_object("helpers/math", b"OBJ", "fp-1")
    assert cache.has_cached_unit("helpers/math", "fp-1") is True
    assert cache.unit_object_path("helpers/math", "fp-1").parent.name == "helpers"


# --------------------------------------------------------------------------
# Path mangling
# --------------------------------------------------------------------------

def test_stdlib_object_path_mangles_separators(cache):
    path = cache.stdlib_object_path("io/stdio", "fp-1")
    assert path.name.startswith("io_stdio.")
    assert path.suffix == ".o"
    assert path.parent == cache.stdlib_path


def test_lib_object_path_lands_in_the_libs_section(cache):
    assert cache.lib_object_path("mylib", "fp-1").parent == cache.libs_path


# --------------------------------------------------------------------------
# Directory lifecycle
# --------------------------------------------------------------------------

def test_prepare_is_idempotent(tmp_path):
    cm = CacheManager(tmp_path, opt_level="mem2reg")
    cm.prepare()
    cm.store_unit_object("main", b"OBJ", "fp-1")
    cm.prepare()  # a second compile in the same directory
    assert cm.has_cached_unit("main", "fp-1"), "prepare() destroyed a cached object"


def test_wipe_removes_cache_directory(cache):
    cache.store_unit_object("main", b"OBJ", "fp-1")
    assert cache.cache_path.exists()
    cache.wipe()
    assert not cache.cache_path.exists()


def test_wipe_tolerates_a_missing_cache(tmp_path):
    """--clean-cache on a project that never had one is a no-op, not an error."""
    CacheManager(tmp_path, opt_level="mem2reg").wipe()


# --------------------------------------------------------------------------
# Compiler-source digest in the global key
# --------------------------------------------------------------------------

def test_global_key_differs_on_compiler_source_digest(tmp_path, monkeypatch):
    """A compiler-source edit must change every cached object's name (a miss by
    construction). compiler_version alone is the static pyproject string, so
    without the digest a codegen fix was invisible to a warm cache (F9)."""
    import sushi_lang.compiler.fingerprint as fp

    monkeypatch.setattr(fp, "_compiler_source_fingerprint", "digest-one")
    key_one = CacheManager(tmp_path, opt_level="mem2reg").global_key

    monkeypatch.setattr(fp, "_compiler_source_fingerprint", "digest-two")
    key_two = CacheManager(tmp_path, opt_level="mem2reg").global_key

    assert key_one != key_two


def test_compiler_source_digest_is_cached_per_process(monkeypatch):
    """The whole-tree content pass runs once; later calls hit the module cache."""
    import sushi_lang.compiler.fingerprint as fp

    monkeypatch.setattr(fp, "_compiler_source_fingerprint", None)
    first = fp.compute_compiler_source_fingerprint()
    assert fp._compiler_source_fingerprint == first
    assert fp.compute_compiler_source_fingerprint() == first
