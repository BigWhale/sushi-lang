"""Cache management for incremental compilation.

Manages the __sushi_cache__/ directory, manifest metadata, and staleness
detection for per-unit object file caching.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from llvmlite import binding as llvm

from sushi_lang import __version__ as compiler_version


# Cache directory name (placed at project root)
CACHE_DIR_NAME = "__sushi_cache__"
MANIFEST_NAME = "cache.json"
UNITS_DIR = "units"
STDLIB_DIR = "stdlib"
LIBS_DIR = "libs"


class CacheManager:
    """Manages the incremental compilation cache directory and manifest.

    The cache stores per-unit .o files alongside a manifest that tracks
    compiler version, platform, and optimization level. A mismatch in
    any global parameter triggers a full cache wipe.

    Directory layout::

        __sushi_cache__/
            cache.json          -- manifest (version, platform, opt)
            units/
                main.o          -- cached object file for main.sushi
                helpers/math.o  -- mirrors source tree
            stdlib/
                io_stdio.o      -- compiled stdlib bitcode
            libs/
                mylib.o         -- compiled library bitcode
    """

    def __init__(self, project_root: Path, opt_level: str = "mem2reg",
                 cache_dir: Optional[Path] = None) -> None:
        self.project_root = project_root
        self.opt_level = opt_level
        self.cache_path = cache_dir or (project_root / CACHE_DIR_NAME)
        self.units_path = self.cache_path / UNITS_DIR
        self.stdlib_path = self.cache_path / STDLIB_DIR
        self.libs_path = self.cache_path / LIBS_DIR
        self._manifest: Optional[dict] = None
        self._target_triple = llvm.get_default_triple()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """Check whether the cache exists and the manifest matches current settings."""
        manifest = self._read_manifest()
        if manifest is None:
            return False
        return (
            manifest.get("compiler_version") == compiler_version
            and manifest.get("target_triple") == self._target_triple
            and manifest.get("opt_level") == self.opt_level
        )

    def ensure_dirs(self) -> None:
        """Create the cache directory structure if it doesn't exist."""
        self.units_path.mkdir(parents=True, exist_ok=True)
        self.stdlib_path.mkdir(parents=True, exist_ok=True)
        self.libs_path.mkdir(parents=True, exist_ok=True)

    def write_manifest(self) -> None:
        """Write (or overwrite) the cache manifest with current settings."""
        self.ensure_dirs()
        manifest = {
            "compiler_version": compiler_version,
            "target_triple": self._target_triple,
            "opt_level": self.opt_level,
        }
        manifest_path = self.cache_path / MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self._manifest = manifest

    def wipe(self) -> None:
        """Remove the entire cache directory."""
        if self.cache_path.exists():
            shutil.rmtree(self.cache_path)
        self._manifest = None

    def invalidate_and_rebuild(self) -> None:
        """Wipe cache and recreate with fresh manifest."""
        self.wipe()
        self.write_manifest()

    # ------------------------------------------------------------------
    # Per-unit object file management
    # ------------------------------------------------------------------

    def unit_object_path(self, unit_name: str) -> Path:
        """Return the cached .o path for a source unit (mirrors source tree)."""
        return self.units_path / (unit_name + ".o")

    def stdlib_object_path(self, stdlib_unit: str) -> Path:
        """Return the cached .o path for a stdlib unit.

        Converts path separators to underscores for flat storage:
        ``io/stdio`` -> ``stdlib/io_stdio.o``
        """
        safe_name = stdlib_unit.replace("/", "_")
        return self.stdlib_path / (safe_name + ".o")

    def lib_object_path(self, lib_name: str) -> Path:
        """Return the cached .o path for a library."""
        safe_name = lib_name.replace("/", "_")
        return self.libs_path / (safe_name + ".o")

    def has_cached_unit(self, unit_name: str, fingerprint: str) -> bool:
        """Check whether a valid cached .o exists for *unit_name* with matching fingerprint."""
        obj_path = self.unit_object_path(unit_name)
        if not obj_path.exists():
            return False
        stored = self._read_unit_fingerprint(unit_name)
        return stored == fingerprint

    def store_unit_object(self, unit_name: str, obj_bytes: bytes, fingerprint: str) -> Path:
        """Store a compiled .o file and its fingerprint for a unit."""
        obj_path = self.unit_object_path(unit_name)
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        obj_path.write_bytes(obj_bytes)
        self._write_unit_fingerprint(unit_name, fingerprint)
        return obj_path

    def has_cached_stdlib(self, stdlib_unit: str, fingerprint: str) -> bool:
        """Check whether a valid cached .o exists for a stdlib module."""
        obj_path = self.stdlib_object_path(stdlib_unit)
        if not obj_path.exists():
            return False
        stored = self._read_stdlib_fingerprint(stdlib_unit)
        return stored == fingerprint

    def store_stdlib_object(self, stdlib_unit: str, obj_bytes: bytes, fingerprint: str) -> Path:
        """Store a compiled stdlib .o file and its fingerprint."""
        obj_path = self.stdlib_object_path(stdlib_unit)
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        obj_path.write_bytes(obj_bytes)
        self._write_stdlib_fingerprint(stdlib_unit, fingerprint)
        return obj_path

    def has_cached_lib(self, lib_name: str, fingerprint: str) -> bool:
        """Check whether a valid cached .o exists for a library."""
        obj_path = self.lib_object_path(lib_name)
        if not obj_path.exists():
            return False
        stored = self._read_lib_fingerprint(lib_name)
        return stored == fingerprint

    def store_lib_object(self, lib_name: str, obj_bytes: bytes, fingerprint: str) -> Path:
        """Store a compiled library .o file and its fingerprint."""
        obj_path = self.lib_object_path(lib_name)
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        obj_path.write_bytes(obj_bytes)
        self._write_lib_fingerprint(lib_name, fingerprint)
        return obj_path

    def collect_all_object_paths(self) -> list[Path]:
        """Collect all cached .o file paths (units + stdlib + libs)."""
        paths = []
        for subdir in (self.units_path, self.stdlib_path, self.libs_path):
            if subdir.exists():
                paths.extend(subdir.rglob("*.o"))
        return paths

    # ------------------------------------------------------------------
    # Fingerprint persistence (stored as .fingerprint sidecar files)
    # ------------------------------------------------------------------

    def _fingerprint_path(self, obj_path: Path) -> Path:
        return obj_path.with_suffix(".fingerprint")

    def _read_unit_fingerprint(self, unit_name: str) -> Optional[str]:
        fp = self._fingerprint_path(self.unit_object_path(unit_name))
        return fp.read_text(encoding="utf-8").strip() if fp.exists() else None

    def _write_unit_fingerprint(self, unit_name: str, fingerprint: str) -> None:
        fp = self._fingerprint_path(self.unit_object_path(unit_name))
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(fingerprint, encoding="utf-8")

    def _read_stdlib_fingerprint(self, stdlib_unit: str) -> Optional[str]:
        fp = self._fingerprint_path(self.stdlib_object_path(stdlib_unit))
        return fp.read_text(encoding="utf-8").strip() if fp.exists() else None

    def _write_stdlib_fingerprint(self, stdlib_unit: str, fingerprint: str) -> None:
        fp = self._fingerprint_path(self.stdlib_object_path(stdlib_unit))
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(fingerprint, encoding="utf-8")

    def _read_lib_fingerprint(self, lib_name: str) -> Optional[str]:
        fp = self._fingerprint_path(self.lib_object_path(lib_name))
        return fp.read_text(encoding="utf-8").strip() if fp.exists() else None

    def _write_lib_fingerprint(self, lib_name: str, fingerprint: str) -> None:
        fp = self._fingerprint_path(self.lib_object_path(lib_name))
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(fingerprint, encoding="utf-8")

    # ------------------------------------------------------------------
    # Manifest I/O
    # ------------------------------------------------------------------

    def _read_manifest(self) -> Optional[dict]:
        if self._manifest is not None:
            return self._manifest
        manifest_path = self.cache_path / MANIFEST_NAME
        if not manifest_path.exists():
            return None
        try:
            self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            return self._manifest
        except (json.JSONDecodeError, OSError):
            return None
