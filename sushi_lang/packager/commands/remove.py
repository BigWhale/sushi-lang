"""nori remove - uninstall a package."""
import argparse

from sushi_lang.packager.installer import InstallError, PackageInstaller
from sushi_lang.packager.paths import find_project_root


def cmd_remove(args: argparse.Namespace) -> int:
    name = args.package
    is_global = getattr(args, "is_global", False)
    installer = PackageInstaller()

    project_root = None if is_global else find_project_root()

    if project_root is not None:
        # Project mode: remove from .sushi_bento/ and nori.toml
        removed = installer.unlink_from_project(project_root, name)
        if not removed:
            raise InstallError("NE3004", name=name)
        from sushi_lang.packager.commands.install import _remove_manifest_dependency
        _remove_manifest_dependency(project_root, name)
        print(f"Removed {name} from project dependencies")
        return 0

    # Global mode
    if not installer.is_installed(name):
        raise InstallError("NE3003", name=name)

    installer.uninstall(name)
    print(f"Removed {name}")
    return 0
