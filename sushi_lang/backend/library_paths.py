"""Where a `.slib` lives: the search order, and the name -> path resolution."""
from __future__ import annotations

import os
import platform
from pathlib import Path

from sushi_lang.backend.library_errors import LibraryError


class LibraryResolver:
    """Finds `.slib` files on disk and caches their manifests."""

    def __init__(self):
        self.search_paths = self._get_search_paths()
        self.loaded_libraries: dict[str, dict] = {}

    def _get_search_paths(self) -> list[Path]:
        """Get library search paths."""
        paths: list[Path] = []

        lib_path = os.environ.get('SUSHI_LIB_PATH')
        if lib_path:
            separator = ';' if platform.system() == 'Windows' else ':'
            for path_str in lib_path.split(separator):
                path_str = path_str.strip()
                if path_str:
                    paths.append(Path(path_str).expanduser())

        from sushi_lang.packager.paths import find_project_root, project_deps_dir
        project_root = find_project_root()
        if project_root:
            deps_dir = project_deps_dir(project_root)
            if deps_dir.is_dir():
                for pkg_dir in sorted(deps_dir.iterdir()):
                    lib_dir = pkg_dir / "lib"
                    if lib_dir.is_dir():
                        paths.append(lib_dir)

        bento_dir = Path.home() / ".sushi" / "bento"
        if bento_dir.is_dir():
            for pkg_dir in sorted(bento_dir.iterdir()):
                lib_dir = pkg_dir / "lib"
                if lib_dir.is_dir():
                    paths.append(lib_dir)

        cwd = Path.cwd()
        if cwd not in paths:
            paths.append(cwd)

        return paths

    def resolve_library(self, lib_path: str) -> Path:
        """Resolve library path to .slib file."""
        if lib_path.startswith("lib/"):
            lib_path = lib_path[4:]

        for search_dir in self.search_paths:
            slib_path = search_dir / f"{lib_path}.slib"
            if slib_path.exists():
                return slib_path

            lib_name = Path(lib_path).name
            slib_path_flat = search_dir / f"{lib_name}.slib"
            if slib_path_flat.exists():
                return slib_path_flat

        search_str = ', '.join(str(p) for p in self.search_paths)
        raise LibraryError("CE3502", lib=lib_path, paths=search_str)
