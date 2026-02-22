"""nori list - show installed packages."""
import argparse

from sushi_lang.packager.installer import PackageInstaller


def _source_label(source: str) -> str:
    if not source:
        return "-"
    if source.startswith("http://") or source.startswith("https://"):
        return "Remote"
    if source == "omakase":
        return "Omakase"
    return "Local"


def cmd_list(args: argparse.Namespace) -> int:
    installer = PackageInstaller()
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
