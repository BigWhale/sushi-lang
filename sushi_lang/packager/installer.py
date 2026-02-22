"""Package installer - install, uninstall, and query packages."""
from __future__ import annotations

import datetime
import shutil
from pathlib import Path

from sushi_lang.packager.archive import PackageArchive
from sushi_lang.packager.constants import BIN_DIR, CACHE_DIR, BENTO_DIR, MANIFEST_NAME
from sushi_lang.packager.manifest import NoriManifest, load_manifest
from sushi_lang.packager.paths import ensure_sushi_home, package_dir


class InstallError(Exception):
    pass


class PackageInstaller:

    def __init__(self):
        ensure_sushi_home()

    def install_from_archive(self, archive_path: Path) -> NoriManifest:
        """Install a package from a .nori archive file."""
        archive_path = archive_path.resolve()
        if not archive_path.exists():
            raise InstallError(f"Archive not found: {archive_path}")

        manifest = PackageArchive.read_manifest(archive_path)
        dest = package_dir(manifest.name)

        if dest.exists():
            shutil.rmtree(dest)

        # Cache the archive
        cached = CACHE_DIR / archive_path.name
        if cached != archive_path:
            shutil.copy2(archive_path, cached)

        # Extract to bento
        extracted = PackageArchive.extract(archive_path, BENTO_DIR)
        # The archive extracts as {name}-{version}/, rename to just {name}/
        if extracted != dest:
            if dest.exists():
                shutil.rmtree(dest)
            extracted.rename(dest)

        # Stamp install source
        self._stamp_source(manifest.name, str(archive_path))

        # Symlink executables
        self._link_executables(manifest.name)

        return manifest

    def install_from_directory(self, source_dir: Path) -> NoriManifest:
        """Install from a directory containing a nori.toml (build in-place)."""
        source_dir = source_dir.resolve()
        manifest = load_manifest(source_dir)
        dest = package_dir(manifest.name)

        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)

        # Copy manifest
        shutil.copy2(source_dir / MANIFEST_NAME, dest / MANIFEST_NAME)

        # Copy libraries
        lib_dir = dest / "lib"
        if manifest.libraries:
            lib_dir.mkdir(exist_ok=True)
            for lib in manifest.libraries:
                src = source_dir / lib
                if src.exists():
                    shutil.copy2(src, lib_dir / src.name)

        # Copy executables
        bin_dir = dest / "bin"
        if manifest.executables:
            bin_dir.mkdir(exist_ok=True)
            for exe in manifest.executables:
                src = source_dir / exe
                if src.exists():
                    shutil.copy2(src, bin_dir / src.name)

        # Copy data
        data_dir = dest / "data"
        if manifest.data:
            data_dir.mkdir(exist_ok=True)
            for data_entry in manifest.data:
                src = source_dir / data_entry
                if src.is_file():
                    shutil.copy2(src, data_dir / src.name)
                elif src.is_dir():
                    shutil.copytree(src, data_dir / src.name, dirs_exist_ok=True)

        # Stamp install source
        self._stamp_source(manifest.name, str(source_dir))

        # Symlink executables
        self._link_executables(manifest.name)

        return manifest

    def uninstall(self, name: str) -> bool:
        """Remove an installed package. Returns True if it was installed."""
        dest = package_dir(name)
        if not dest.exists():
            return False

        # Remove executable symlinks
        self._unlink_executables(name)

        # Remove package directory
        shutil.rmtree(dest)

        # Remove cached archive if present
        for cached in CACHE_DIR.glob(f"{name}-*.nori"):
            cached.unlink()

        return True

    def is_installed(self, name: str) -> bool:
        return package_dir(name).exists()

    def get_installed_packages(self) -> list[NoriManifest]:
        """List all installed packages."""
        packages = []
        if not BENTO_DIR.exists():
            return packages
        for pkg_dir in sorted(BENTO_DIR.iterdir()):
            manifest_path = pkg_dir / MANIFEST_NAME
            if manifest_path.exists():
                import tomllib
                with open(manifest_path, "rb") as f:
                    data = tomllib.load(f)
                from sushi_lang.packager.manifest import _parse_manifest
                try:
                    packages.append(_parse_manifest(data))
                except Exception:
                    pass
        return packages

    def _link_executables(self, pkg_name: str) -> None:
        """Create symlinks in ~/.sushi/bin/ for package executables."""
        bin_src = package_dir(pkg_name) / "bin"
        if not bin_src.exists():
            return
        for exe in bin_src.iterdir():
            if exe.is_file():
                link = BIN_DIR / exe.name
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(exe)

    def _unlink_executables(self, pkg_name: str) -> None:
        """Remove symlinks in ~/.sushi/bin/ that point to this package."""
        bin_src = package_dir(pkg_name) / "bin"
        if not bin_src.exists():
            return
        for exe in bin_src.iterdir():
            link = BIN_DIR / exe.name
            if link.is_symlink() and link.resolve().parent == bin_src.resolve():
                link.unlink()

    def _stamp_source(self, pkg_name: str, source: str) -> None:
        """Append [install] section with source info to the installed manifest."""
        manifest_path = package_dir(pkg_name) / MANIFEST_NAME
        if not manifest_path.exists():
            return
        today = datetime.date.today().isoformat()
        with open(manifest_path, "a") as f:
            f.write(f"\n[install]\n")
            f.write(f'source = "{source}"\n')
            f.write(f'date = "{today}"\n')
