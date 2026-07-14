"""The incremental cache must be safe for concurrent compilers (issue #196).

Two `sushic` processes compiling two files in the same directory share one
`__sushi_cache__` (it is rooted at the source file's parent, `pipeline.py`), and the
test harness runs four of them at a time. That is an ordinary parallel build, not an
exotic setup, and the cache was not safe for it.

Reproduced on the broken code, at ~1 failure per 240 concurrent compiles::

    error [CE0000]: internal compiler error: OSError: [Errno 66] Directory not empty:
      PosixPath('.../tests/stdlib/__sushi_cache__')

`wipe()`'s `shutil.rmtree` was walking a directory a peer was still creating files in.
Exit 2 -- which the harness reports as "Compilation failed", the symptom in #196.

The invariant these tests pin: **a compile never destroys a directory a peer is using.**
Staleness is handled by keying an object on what produced it, so nothing has to be
thrown away; see `CacheManager._global_key`.
"""
from __future__ import annotations

import threading

import pytest

from sushi_lang.compiler.cache import CacheManager


# --------------------------------------------------------------------------
# R-B: a peer's cold start must not delete objects another compiler just stored
# --------------------------------------------------------------------------

def test_peer_prepare_does_not_destroy_a_stored_object(tmp_path):
    """A peer that sees the cache as invalid must not delete what we stored in it.

    The old `prepare()` answered "this cache is not mine" by deleting it. Two opt
    levels in one directory is the deterministic way to reach that branch; the cold
    start below reaches the same branch by timing. A cache *miss* is the correct
    response -- the other compiler's objects are none of our business.
    """
    mem2reg = CacheManager(tmp_path, opt_level="mem2reg")
    mem2reg.prepare()
    mem2reg.store_unit_object("main", b"MEM2REG-OBJ", "fp-1")

    o2 = CacheManager(tmp_path, opt_level="O2")
    o2.prepare()

    # O2 must not reuse mem2reg's object...
    assert not o2.has_cached_unit("main", "fp-1")
    # ...and must not have destroyed it either.
    assert mem2reg.has_cached_unit("main", "fp-1")


# --------------------------------------------------------------------------
# R-A: concurrent cold starts must not crash
# --------------------------------------------------------------------------

def test_concurrent_cold_start_does_not_raise(tmp_path):
    """N compilers starting at once against one cold cache.

    On the broken code this raced inside `shutil.rmtree`: one thread deleting the
    tree while another created files in it -> OSError(Errno 66) / FileNotFoundError,
    surfaced to the user as CE0000.
    """
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def compile_like(i: int) -> None:
        try:
            start.wait(timeout=10)
            for round_ in range(15):
                cache = CacheManager(tmp_path, opt_level="mem2reg")
                cache.prepare()
                cache.store_unit_object(f"unit{i}", b"X" * 4096, f"fp-{i}-{round_}")
                cache.store_unit_object("collections/iter", b"SHARED" * 512, "fp-shared")
        except BaseException as exc:  # noqa: BLE001 - the assertion IS "nothing escaped"
            errors.append(exc)

    threads = [threading.Thread(target=compile_like, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"concurrent cold start raised: {errors[:3]}"


def test_concurrent_writers_never_expose_a_partial_object(tmp_path):
    """A reader must see a stored object whole, or not at all.

    `write_bytes` truncates and then writes: for the duration, the file on disk is
    short. A peer linking at that instant hands `cc` a truncated object. Publishing
    via os.replace() makes the store atomic, so the path only ever names complete
    bytes.
    """
    payload = b"OBJ" * 200_000  # big enough that a non-atomic write is visibly torn
    stop = threading.Event()
    torn: list[int] = []
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            cache = CacheManager(tmp_path, opt_level="mem2reg")
            cache.prepare()
            for _ in range(40):
                cache.store_unit_object("collections/iter", payload, "fp-shared")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            stop.set()

    def reader() -> None:
        cache = CacheManager(tmp_path, opt_level="mem2reg")
        path = cache.unit_object_path("collections/iter", "fp-shared")
        while not stop.is_set():
            try:
                data = path.read_bytes()
            except OSError:
                continue  # not published yet
            if data and len(data) != len(payload):
                torn.append(len(data))

    w = [threading.Thread(target=writer) for _ in range(3)]
    r = [threading.Thread(target=reader) for _ in range(3)]
    for t in w + r:
        t.start()
    for t in w:
        t.join(timeout=60)
    stop.set()
    for t in r:
        t.join(timeout=10)

    assert not errors, f"concurrent store raised: {errors[:3]}"
    assert not torn, f"a reader saw a partial object (sizes {torn[:5]}, want {len(payload)})"


# --------------------------------------------------------------------------
# The mechanism that makes the above possible: nothing needs to be thrown away
# --------------------------------------------------------------------------

def test_object_path_is_keyed_by_fingerprint(tmp_path):
    """Two fingerprints for one unit are two files, so neither has to be evicted."""
    cache = CacheManager(tmp_path, opt_level="mem2reg")
    assert (cache.unit_object_path("collections/iter", "fp-a")
            != cache.unit_object_path("collections/iter", "fp-b"))


def test_object_path_is_keyed_by_global_params(tmp_path):
    """...and so are two opt levels, which is what retires the manifest wipe."""
    mem2reg = CacheManager(tmp_path, opt_level="mem2reg")
    o2 = CacheManager(tmp_path, opt_level="O2")
    assert (mem2reg.unit_object_path("main", "fp-1")
            != o2.unit_object_path("main", "fp-1"))


@pytest.mark.parametrize("store,path,has", [
    ("store_unit_object", "unit_object_path", "has_cached_unit"),
    ("store_stdlib_object", "stdlib_object_path", "has_cached_stdlib"),
    ("store_lib_object", "lib_object_path", "has_cached_lib"),
])
def test_store_round_trips_and_leaves_no_temp_files(tmp_path, store, path, has):
    """Every cache section stores atomically and cleans up after itself."""
    cache = CacheManager(tmp_path, opt_level="mem2reg")
    cache.prepare()

    getattr(cache, store)("thing", b"BYTES", "fp-1")

    assert getattr(cache, has)("thing", "fp-1") is True
    assert getattr(cache, has)("thing", "fp-other") is False
    assert getattr(cache, path)("thing", "fp-1").read_bytes() == b"BYTES"
    assert not list(cache.cache_path.rglob("*.tmp")), "an atomic-write temp file leaked"
