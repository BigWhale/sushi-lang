"""nori info - show details about an installed package."""
import argparse

from sushi_lang.packager.installer import PackageInstaller
from sushi_lang.packager.paths import package_dir


def cmd_info(args: argparse.Namespace) -> int:
    name = args.package
    installer = PackageInstaller()

    if not installer.is_installed(name):
        print(f"Package '{name}' is not installed.")
        return 1

    packages = installer.get_installed_packages()
    manifest = next((p for p in packages if p.name == name), None)
    if manifest is None:
        print(f"Package '{name}' has a corrupted manifest.")
        return 1

    pkg_path = package_dir(name)

    print(f"Package:     {manifest.name}")
    print(f"Version:     {manifest.version}")
    if manifest.description:
        print(f"Description: {manifest.description}")
    if manifest.author:
        print(f"Author:      {manifest.author}")
    if manifest.license:
        print(f"License:     {manifest.license}")
    if manifest.source:
        print(f"Source:      {manifest.source}")
    print(f"Location:    {pkg_path}")

    # List installed files
    lib_dir = pkg_path / "lib"
    bin_dir = pkg_path / "bin"
    data_dir = pkg_path / "data"

    files = []
    if lib_dir.exists():
        files.extend(f"  lib/{f.name}" for f in sorted(lib_dir.iterdir()) if f.is_file())
    if bin_dir.exists():
        files.extend(f"  bin/{f.name}" for f in sorted(bin_dir.iterdir()) if f.is_file())
    if data_dir.exists():
        for f in sorted(data_dir.rglob("*")):
            if f.is_file():
                files.append(f"  data/{f.relative_to(data_dir)}")

    if files:
        print(f"Files:")
        for f in files:
            print(f)

    return 0
