"""Cache management for incremental compilation.

Manages the ``__sushi_cache__/`` directory and staleness detection for per-unit
object-file caching.

**The cache is shared, so it is never destroyed on the compile path.** It is rooted
at the source file's parent directory (see ``pipeline.py``), so two ``sushic``
processes compiling two files in one directory -- an ordinary parallel build, and
what the test harness does with four jobs -- share it. The cache used to answer "this
entry was built with different settings" by deleting the whole directory, which raced
peers that were reading or writing inside it (issue #196: ``shutil.rmtree`` walking a
tree another process was still creating files in, surfaced as CE0000).

Instead, **an object is keyed by everything that produced it** -- the global
parameters (compiler version, target triple, opt level) and the unit's semantic
fingerprint -- so a stale entry can never be a false hit and nothing has to be
evicted to stay correct. Different settings simply name different files. Publishing
is atomic (``os.replace``), so a peer reading the path sees complete bytes or no file
at all, never a truncated object.

Entries for settings you no longer use are dead weight, not a correctness problem;
``sushic --clean-cache`` prunes them.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
from pathlib import Path
from typing import Optional

from llvmlite import binding as llvm

from sushi_lang import __version__ as compiler_version


# Cache directory name (placed at project root)
CACHE_DIR_NAME = "__sushi_cache__"
UNITS_DIR = "units"
STDLIB_DIR = "stdlib"
LIBS_DIR = "libs"

# Length of the hex digests embedded in cached object names. Collision risk is
# negligible and short names keep the cache readable.
_KEY_LEN = 12


class CacheManager:
    """Manages the incremental compilation cache directory.

    Directory layout::

        __sushi_cache__/
            units/
                main.<global>.<fingerprint>.o          -- mirrors the source tree
                helpers/math.<global>.<fingerprint>.o
            stdlib/
                io_stdio.<global>.<fingerprint>.o
            libs/
                mylib.<global>.<fingerprint>.o

    ``<global>`` digests the compiler version, target triple and opt level;
    ``<fingerprint>`` digests the unit itself. Both are in the name, so a hit is
    exactly "an object built from this input, by this compiler, with these settings".
    """

    def __init__(self, project_root: Path, opt_level: str = "mem2reg",
                 cache_dir: Optional[Path] = None) -> None:
        self.project_root = project_root
        self.opt_level = opt_level
        self.cache_path = cache_dir or (project_root / CACHE_DIR_NAME)
        self.units_path = self.cache_path / UNITS_DIR
        self.stdlib_path = self.cache_path / STDLIB_DIR
        self.libs_path = self.cache_path / LIBS_DIR
        self._target_triple = llvm.get_default_triple()

    @property
    def global_key(self) -> str:
        """Digest of the settings every cached object depends on.

        This is what the manifest used to check (and wipe the cache over). Folding it
        into the object name turns "the cache is stale" from an eviction into a miss.
        """
        material = f"{compiler_version}|{self._target_triple}|{self.opt_level}"
        return hashlib.sha1(material.encode("utf-8")).hexdigest()[:_KEY_LEN]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare(self) -> None:
        """Ready the cache directory for a compile.

        The single entry point a compiler driver needs. Creating the directories is
        all it takes -- and it is idempotent and safe to race, which is the point.
        """
        self.ensure_dirs()

    def ensure_dirs(self) -> None:
        """Create the cache directory structure if it doesn't exist."""
        for path in (self.units_path, self.stdlib_path, self.libs_path):
            path.mkdir(parents=True, exist_ok=True)

    def wipe(self) -> None:
        """Remove the entire cache directory.

        Only for the explicit, single-process ``--clean-cache``. **Never call this on
        the compile path** -- a peer may be reading the tree (#196). ``ignore_errors``
        because even here a concurrent compile may be creating files underneath us,
        and failing to prune is not worth an error.
        """
        shutil.rmtree(self.cache_path, ignore_errors=True)

    # ------------------------------------------------------------------
    # Per-unit object file management
    # ------------------------------------------------------------------

    def unit_object_path(self, unit_name: str, fingerprint: str) -> Path:
        """Cached .o path for a source unit (mirrors the source tree)."""
        return self._object_path(self.units_path, unit_name, fingerprint)

    def stdlib_object_path(self, stdlib_unit: str, fingerprint: str) -> Path:
        """Cached .o path for a stdlib unit (``io/stdio`` -> ``stdlib/io_stdio.*.o``)."""
        return self._object_path(self.stdlib_path, stdlib_unit.replace("/", "_"), fingerprint)

    def lib_object_path(self, lib_name: str, fingerprint: str) -> Path:
        """Cached .o path for a library."""
        return self._object_path(self.libs_path, lib_name.replace("/", "_"), fingerprint)

    def has_cached_unit(self, unit_name: str, fingerprint: str) -> bool:
        return self.unit_object_path(unit_name, fingerprint).exists()

    def has_cached_stdlib(self, stdlib_unit: str, fingerprint: str) -> bool:
        return self.stdlib_object_path(stdlib_unit, fingerprint).exists()

    def has_cached_lib(self, lib_name: str, fingerprint: str) -> bool:
        return self.lib_object_path(lib_name, fingerprint).exists()

    def store_unit_object(self, unit_name: str, obj_bytes: bytes, fingerprint: str) -> Path:
        return self._store(self.unit_object_path(unit_name, fingerprint), obj_bytes)

    def store_stdlib_object(self, stdlib_unit: str, obj_bytes: bytes, fingerprint: str) -> Path:
        return self._store(self.stdlib_object_path(stdlib_unit, fingerprint), obj_bytes)

    def store_lib_object(self, lib_name: str, obj_bytes: bytes, fingerprint: str) -> Path:
        return self._store(self.lib_object_path(lib_name, fingerprint), obj_bytes)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _object_path(self, section: Path, name: str, fingerprint: str) -> Path:
        return section / f"{name}.{self.global_key}.{fingerprint[:_KEY_LEN]}.o"

    def _store(self, obj_path: Path, obj_bytes: bytes) -> Path:
        """Publish an object atomically.

        A plain ``write_bytes`` truncates first, so for the length of the write the
        path names a short file -- and a peer linking at that instant hands ``cc`` a
        truncated object. Write to a private temp beside the target, then rename:
        ``os.replace`` is atomic, so the path only ever names complete bytes.
        """
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = obj_path.with_name(
            f"{obj_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            tmp_path.write_bytes(obj_bytes)
            os.replace(tmp_path, obj_path)
        finally:
            # os.replace consumed it on the happy path; this is for the failure path.
            tmp_path.unlink(missing_ok=True)
        return obj_path
