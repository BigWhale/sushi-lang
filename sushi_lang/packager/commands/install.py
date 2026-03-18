"""nori install - install a package from a local or remote source."""
import argparse
from pathlib import Path

from sushi_lang.packager.constants import BIN_DIR, ARCHIVE_EXT, MANIFEST_NAME
from sushi_lang.packager.installer import PackageInstaller
from sushi_lang.packager.manifest import NoriManifest
from sushi_lang.packager.paths import find_project_root, project_deps_dir
from sushi_lang.packager.repository import resolve_repository


def _is_path(value: str) -> bool:
    """Check if the value looks like a filesystem path."""
    return value.startswith(("./", "../", "/", "~"))


def _update_manifest_dependencies(project_root: Path, name: str, version: str) -> None:
    """Add or update a dependency in the project's nori.toml."""
    manifest_path = project_root / MANIFEST_NAME
    content = manifest_path.read_text()

    dep_line = f'{name} = "{version}"'

    if "[dependencies]" not in content:
        # Add section at end
        content = content.rstrip() + f"\n\n[dependencies]\n{dep_line}\n"
    else:
        lines = content.split("\n")
        new_lines = []
        in_deps = False
        replaced = False
        for line in lines:
            stripped = line.strip()
            if stripped == "[dependencies]":
                in_deps = True
                new_lines.append(line)
                continue
            if in_deps and stripped.startswith("["):
                # Entering a new section; if not replaced yet, insert before it
                if not replaced:
                    new_lines.append(dep_line)
                    replaced = True
                in_deps = False
            if in_deps and stripped.startswith(f"{name} ") or in_deps and stripped.startswith(f"{name}="):
                new_lines.append(dep_line)
                replaced = True
                continue
            new_lines.append(line)
        if in_deps and not replaced:
            new_lines.append(dep_line)
        content = "\n".join(new_lines)

    manifest_path.write_text(content)


def _remove_manifest_dependency(project_root: Path, name: str) -> None:
    """Remove a dependency from the project's nori.toml."""
    manifest_path = project_root / MANIFEST_NAME
    content = manifest_path.read_text()
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{name} ") or stripped.startswith(f"{name}="):
            continue
        new_lines.append(line)
    manifest_path.write_text("\n".join(new_lines))


def cmd_install(args: argparse.Namespace) -> int:
    package = getattr(args, "package", None)
    source = getattr(args, "source", None)
    is_global = getattr(args, "is_global", False)

    # Validate "from" keyword if source is provided
    if source is not None and args.from_keyword != "from":
        print("Usage: nori install <package> from <source>")
        return 1

    # Detect project context
    project_root = None if is_global else find_project_root()

    # Bare `nori install` (no package arg) -> restore project deps
    if package is None:
        return _restore_project_deps(project_root)

    # Determine source type
    if source is not None:
        return _install_from_source(package, source, project_root)

    # Path-like argument -> local install
    if _is_path(package):
        path = Path(package).expanduser().resolve()
        if not path.exists():
            print(f"Path not found: {package}")
            return 1
        if path.is_file() and path.name.endswith(ARCHIVE_EXT):
            return _install_archive(path, project_root)
        if path.is_dir():
            return _install_directory(path, project_root)
        print(f"Cannot install from: {package}")
        return 1

    # Package name -> remote repository
    repository = resolve_repository(args)
    return _install_remote(package, repository)


def _restore_project_deps(project_root: Path | None) -> int:
    """Restore all dependencies from nori.toml into .sushi_bento/."""
    if project_root is None:
        print("Not in a Sushi project (no nori.toml found).")
        print("Usage: nori install <package>")
        return 1

    from sushi_lang.packager.manifest import load_manifest
    manifest = load_manifest(project_root)
    if not manifest.dependencies:
        print("No dependencies in nori.toml.")
        return 0

    installer = PackageInstaller()
    restored = 0
    missing = []
    for name, version in manifest.dependencies.items():
        if installer.is_in_store(name, version):
            installer.link_to_project(project_root, name, version)
            restored += 1
        else:
            missing.append(f"{name} v{version}")

    if restored:
        print(f"Restored {restored} dependency(ies) to {project_deps_dir(project_root)}")
    if missing:
        print(f"Missing from store (install manually): {', '.join(missing)}")
        return 1
    return 0


def _install_from_source(package: str, source: str, project_root: Path | None) -> int:
    source_path = Path(source).resolve()
    if not source_path.exists():
        print(f"Source not found: {source}")
        return 1

    if source_path.is_file() and source_path.name.endswith(ARCHIVE_EXT):
        return _install_archive(source_path, project_root)

    if source_path.is_dir():
        # Look for a .nori file matching the package name in the directory
        candidates = list(source_path.glob(f"{package}-*{ARCHIVE_EXT}"))
        if candidates:
            # Use the latest (sorted by name, last is highest version)
            return _install_archive(sorted(candidates)[-1], project_root)
        # Try as a source directory with nori.toml
        if (source_path / "nori.toml").exists():
            return _install_directory(source_path, project_root)
        print(f"No .nori archive for '{package}' found in {source_path}")
        return 1

    print(f"Cannot install from: {source}")
    return 1


def _install_archive(path: Path, project_root: Path | None) -> int:
    installer = PackageInstaller()
    if project_root is not None:
        manifest = installer.install_archive_to_store(path)
        installer.link_to_project(project_root, manifest.name, manifest.version)
        _update_manifest_dependencies(project_root, manifest.name, manifest.version)
        print(f"Installed {manifest.name} v{manifest.version} (project dependency)")
    else:
        manifest = installer.install_from_archive(path)
        print(f"Installed {manifest.name} v{manifest.version} (global)")
        _print_path_hint()
    return 0


def _install_directory(path: Path, project_root: Path | None) -> int:
    installer = PackageInstaller()
    if project_root is not None:
        manifest = installer.install_directory_to_store(path)
        installer.link_to_project(project_root, manifest.name, manifest.version)
        _update_manifest_dependencies(project_root, manifest.name, manifest.version)
        print(f"Installed {manifest.name} v{manifest.version} (project dependency)")
    else:
        manifest = installer.install_from_directory(path)
        print(f"Installed {manifest.name} v{manifest.version} (global)")
        _print_path_hint()
    return 0


def _install_remote(package: str, repository: str) -> int:
    """Stub for installing a package from a remote repository."""
    print(f"Remote install from {repository} is not yet implemented.")
    print(f"Install from a local source: nori install <package> from <path>")
    return 1


def _print_path_hint() -> None:
    bin_dir = str(BIN_DIR)
    print(f"  Executables available in: {bin_dir}")
    print(f"  Add to PATH: export PATH=\"{bin_dir}:$PATH\"")
