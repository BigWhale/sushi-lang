"""nori build - create a .nori package archive."""
import argparse
from pathlib import Path

from sushi_lang.packager.archive import PackageArchive
from sushi_lang.packager.manifest import load_manifest


def cmd_build(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    base_dir = Path.cwd()
    output_dir = base_dir / "dist"

    file_count = len(manifest.libraries) + len(manifest.executables) + len(manifest.data)
    if file_count == 0:
        print("Warning: no files listed in [files] section.")

    archive_path = PackageArchive.create(manifest, base_dir, output_dir)
    size = archive_path.stat().st_size
    size_str = _format_size(size)

    print(f"Built {manifest.name} v{manifest.version}")
    print(f"  {file_count} file(s), {size_str}")
    print(f"  {archive_path}")
    return 0


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
