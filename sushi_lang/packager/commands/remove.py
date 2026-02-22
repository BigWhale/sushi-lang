"""nori remove - uninstall a package."""
import argparse

from sushi_lang.packager.installer import PackageInstaller


def cmd_remove(args: argparse.Namespace) -> int:
    name = args.package
    installer = PackageInstaller()

    if not installer.is_installed(name):
        print(f"Package '{name}' is not installed.")
        return 1

    installer.uninstall(name)
    print(f"Removed {name}")
    return 0
