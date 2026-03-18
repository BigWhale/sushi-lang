"""nori list - show installed packages."""
import argparse

from sushi_lang.packager.installer import PackageInstaller
from sushi_lang.packager.paths import find_project_root


def _source_label(source: str) -> str:
    if not source:
        return "-"
    if source.startswith("http://") or source.startswith("https://"):
        return "Remote"
    if source == "omakase":
        return "Omakase"
    return "Local"


def cmd_list(args: argparse.Namespace) -> int:
    is_global = getattr(args, "is_global", False)
    installer = PackageInstaller()

    project_root = None if is_global else find_project_root()

    if project_root is not None:
        return _list_project(installer, project_root)
    return _list_global(installer)


def _list_project(installer: PackageInstaller, project_root) -> int:
    packages = installer.get_project_packages(project_root)
    if not packages:
        print("No project dependencies installed.")
        print("Use 'nori list --global' to see global packages.")
        return 0

    print(f"{'Package':<24} {'Version':<10} {'Description'}")
    print(f"{'-' * 24} {'-' * 10} {'-' * 30}")
    for pkg in packages:
        desc = pkg.description[:30] if pkg.description else ""
        print(f"{pkg.name:<24} {pkg.version:<10} {desc}")

    print(f"\n{len(packages)} project dependency(ies).")
    print("Use 'nori list --global' to see global packages.")
    return 0


def _list_global(installer: PackageInstaller) -> int:
    packages = installer.get_installed_packages()
    if not packages:
        print("No packages installed.")
        return 0

    print(f"{'Package':<24} {'Version':<10} {'Source':<10} {'Description'}")
    print(f"{'-' * 24} {'-' * 10} {'-' * 10} {'-' * 30}")
    for pkg in packages:
        label = _source_label(pkg.source)
        desc = pkg.description[:30] if pkg.description else ""
        print(f"{pkg.name:<24} {pkg.version:<10} {label:<10} {desc}")

    print(f"\n{len(packages)} package(s) installed.")
    print("Use 'nori info <package>' for details.")
    return 0
