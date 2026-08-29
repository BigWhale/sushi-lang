"""Cache management for incremental compilation."""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
from pathlib import Path
from typing import Optional

from sushi_lang.backend.platform_detect import default_triple

from sushi_lang import __version__ as compiler_version


CACHE_DIR_NAME = "__sushi_cache__"
# Where a source library's units are written out. They need to be REAL files:
# diagnostics read a unit's source off disk to render a caret, and the unit
# fingerprint hashes those same bytes to decide what to rebuild.
LIBSRC_DIR = "libsrc"
UNITS_DIR = "units"
STDLIB_DIR = "stdlib"
LIBS_DIR = "libs"

_KEY_LEN = 12


class CacheManager:
    """Manages the incremental compilation cache directory."""

    def __init__(self, project_root: Path, opt_level: str = "mem2reg",
                 cache_dir: Optional[Path] = None) -> None:
        self.project_root = project_root
        self.opt_level = opt_level
        self.cache_path = cache_dir or (project_root / CACHE_DIR_NAME)
        self.units_path = self.cache_path / UNITS_DIR
        self.stdlib_path = self.cache_path / STDLIB_DIR
        self.libs_path = self.cache_path / LIBS_DIR
        self.libsrc_path = self.cache_path / LIBSRC_DIR
        self._target_triple = default_triple()

    @property
    def global_key(self) -> str:
        """Digest of the settings every cached object depends on."""
        from sushi_lang.compiler.fingerprint import compute_compiler_source_fingerprint
        material = (
            f"{compiler_version}|{self._target_triple}|{self.opt_level}"
            f"|{compute_compiler_source_fingerprint()}"
        )
        return hashlib.sha1(material.encode("utf-8")).hexdigest()[:_KEY_LEN]

    def prepare(self) -> None:
        """Ready the cache directory for a compile."""
        self.ensure_dirs()

    def ensure_dirs(self) -> None:
        """Create the cache directory structure if it doesn't exist."""
        for path in (self.units_path, self.stdlib_path, self.libs_path,
                     self.libsrc_path):
            path.mkdir(parents=True, exist_ok=True)

    def wipe(self) -> None:
        """Remove the entire cache directory."""
        shutil.rmtree(self.cache_path, ignore_errors=True)

    def unit_object_path(self, unit_name: str, fingerprint: str) -> Path:
        """Cached .o path for a source unit (mirrors the source tree)."""
        return self._object_path(self.units_path, unit_name, fingerprint)

    def stdlib_object_path(self, stdlib_unit: str, fingerprint: str) -> Path:
        """Cached .o path for a stdlib unit (``io/stdio`` -> ``stdlib/io_stdio.*.o``)."""
        return self._object_path(self.stdlib_path, stdlib_unit.replace("/", "_"), fingerprint)

    def lib_object_path(self, lib_name: str, fingerprint: str) -> Path:
        """Cached .o path for a library."""
        return self._object_path(self.libs_path, lib_name.replace("/", "_"), fingerprint)

    def library_source_dir(self, lib_name: str) -> Path:
        """Directory holding one source library's materialized units."""
        return self.libsrc_path / lib_name.replace("/", "_")

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

    def _object_path(self, section: Path, name: str, fingerprint: str) -> Path:
        return section / f"{name}.{self.global_key}.{fingerprint[:_KEY_LEN]}.o"

    def _store(self, obj_path: Path, obj_bytes: bytes) -> Path:
        """Publish an object atomically."""
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = obj_path.with_name(
            f"{obj_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            tmp_path.write_bytes(obj_bytes)
            os.replace(tmp_path, obj_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        return obj_path
