"""nori install - install a package from a local or remote source."""
import argparse
from pathlib import Path

from sushi_lang.packager.constants import BIN_DIR, ARCHIVE_EXT
from sushi_lang.packager.installer import PackageInstaller


def cmd_install(args: argparse.Namespace) -> int:
    package = args.package
    source = args.source

    # Validate "from" keyword if source is provided
    if source is not None and args.from_keyword != "from":
        print("Usage: nori install <package> from <source>")
        return 1

    # Determine source type
    if source is not None:
        return _install_from_source(package, source)

    # No source - check if package arg is a path
    path = Path(package)
    if path.exists():
        if path.is_file() and path.name.endswith(ARCHIVE_EXT):
            return _install_archive(path)
        if path.is_dir():
            return _install_directory(path)

    # Default: omakase registry
    print(f"Omakase repository is not yet available.")
    print(f"Install from a local source: nori install <package> from <path>")
    return 1


def _install_from_source(package: str, source: str) -> int:
    source_path = Path(source).resolve()
    if not source_path.exists():
        print(f"Source not found: {source}")
        return 1

    if source_path.is_file() and source_path.name.endswith(ARCHIVE_EXT):
        return _install_archive(source_path)

    if source_path.is_dir():
        # Look for a .nori file matching the package name in the directory
        candidates = list(source_path.glob(f"{package}-*{ARCHIVE_EXT}"))
        if candidates:
            # Use the latest (sorted by name, last is highest version)
            return _install_archive(sorted(candidates)[-1])
        # Try as a source directory with nori.toml
        if (source_path / "nori.toml").exists():
            return _install_directory(source_path)
        print(f"No .nori archive for '{package}' found in {source_path}")
        return 1

    print(f"Cannot install from: {source}")
    return 1


def _install_archive(path: Path) -> int:
    installer = PackageInstaller()
    manifest = installer.install_from_archive(path)
    print(f"Installed {manifest.name} v{manifest.version}")
    _print_path_hint()
    return 0


def _install_directory(path: Path) -> int:
    installer = PackageInstaller()
    manifest = installer.install_from_directory(path)
    print(f"Installed {manifest.name} v{manifest.version}")
    _print_path_hint()
    return 0


def _print_path_hint() -> None:
    bin_dir = str(BIN_DIR)
    print(f"  Executables available in: {bin_dir}")
    print(f"  Add to PATH: export PATH=\"{bin_dir}:$PATH\"")
