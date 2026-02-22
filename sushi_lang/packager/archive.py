"""Nori package archive (.nori) creation and extraction."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

from sushi_lang.packager.constants import MANIFEST_NAME
from sushi_lang.packager.manifest import NoriManifest, load_manifest_from_string


class ArchiveError(Exception):
    pass


class PackageArchive:

    @staticmethod
    def create(manifest: NoriManifest, base_dir: Path, output_dir: Path) -> Path:
        """Create a .nori archive from a manifest and its files.

        Args:
            manifest: Validated manifest.
            base_dir: Directory containing nori.toml and referenced files.
            output_dir: Directory to write the archive to.

        Returns:
            Path to the created .nori file.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        archive_path = output_dir / f"{manifest.archive_name}.nori"
        prefix = manifest.archive_name

        with tarfile.open(archive_path, "w:gz") as tar:
            # Add manifest
            manifest_path = base_dir / MANIFEST_NAME
            tar.add(manifest_path, arcname=f"{prefix}/{MANIFEST_NAME}")

            # Add libraries
            for lib in manifest.libraries:
                _add_file(tar, base_dir, lib, f"{prefix}/lib", prefix)

            # Add executables (preserve permissions)
            for exe in manifest.executables:
                _add_file(tar, base_dir, exe, f"{prefix}/bin", prefix, executable=True)

            # Add data files/directories
            for data_entry in manifest.data:
                _add_data(tar, base_dir, data_entry, f"{prefix}/data", prefix)

        return archive_path

    @staticmethod
    def extract(archive_path: Path, dest_dir: Path) -> Path:
        """Extract a .nori archive.

        Args:
            archive_path: Path to the .nori file.
            dest_dir: Directory to extract into.

        Returns:
            Path to the extracted package directory.
        """
        with tarfile.open(archive_path, "r:gz") as tar:
            # Find the top-level directory name
            members = tar.getnames()
            if not members:
                raise ArchiveError("Empty archive")
            top_dir = members[0].split("/")[0]

            tar.extractall(path=dest_dir, filter="data")

        return dest_dir / top_dir

    @staticmethod
    def read_manifest(archive_path: Path) -> NoriManifest:
        """Read the manifest from a .nori archive without full extraction."""
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith(f"/{MANIFEST_NAME}"):
                    f = tar.extractfile(member)
                    if f is None:
                        raise ArchiveError(f"Cannot read {MANIFEST_NAME} from archive")
                    return load_manifest_from_string(f.read().decode("utf-8"))
        raise ArchiveError(f"No {MANIFEST_NAME} found in archive")


def _add_file(
    tar: tarfile.TarFile,
    base_dir: Path,
    file_path: str,
    arc_subdir: str,
    prefix: str,
    executable: bool = False,
) -> None:
    """Add a single file to the archive."""
    full_path = base_dir / file_path
    if not full_path.exists():
        raise ArchiveError(f"File not found: {file_path}")
    if not full_path.is_file():
        raise ArchiveError(f"Not a file: {file_path}")
    arcname = f"{arc_subdir}/{full_path.name}"
    info = tar.gettarinfo(full_path, arcname=arcname)
    if executable:
        info.mode = 0o755
    with open(full_path, "rb") as f:
        tar.addfile(info, f)


def _add_data(
    tar: tarfile.TarFile,
    base_dir: Path,
    data_entry: str,
    arc_subdir: str,
    prefix: str,
) -> None:
    """Add a data file or directory to the archive."""
    full_path = base_dir / data_entry
    if not full_path.exists():
        raise ArchiveError(f"Data path not found: {data_entry}")
    if full_path.is_file():
        arcname = f"{arc_subdir}/{full_path.name}"
        tar.add(full_path, arcname=arcname)
    elif full_path.is_dir():
        for child in sorted(full_path.rglob("*")):
            if child.is_file():
                rel = child.relative_to(full_path)
                arcname = f"{arc_subdir}/{full_path.name}/{rel}"
                tar.add(child, arcname=arcname)
