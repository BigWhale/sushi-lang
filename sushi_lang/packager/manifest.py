"""Nori manifest (nori.toml) loading and validation."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from sushi_lang.packager.constants import MANIFEST_NAME

# Package name: lowercase alphanumeric + hyphens, 1-64 chars
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9\-]{0,63}$")

# Version: major.minor.patch
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class ManifestError(Exception):
    pass


@dataclass
class NoriManifest:
    name: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    libraries: list[str] = field(default_factory=list)
    executables: list[str] = field(default_factory=list)
    data: list[str] = field(default_factory=list)
    source: str = ""

    def validate(self) -> None:
        if not NAME_PATTERN.match(self.name):
            raise ManifestError(
                f"Invalid package name '{self.name}'. "
                "Must be 1-64 chars, lowercase alphanumeric and hyphens, starting with a letter."
            )
        if not VERSION_PATTERN.match(self.version):
            raise ManifestError(
                f"Invalid version '{self.version}'. Must be in major.minor.patch format (e.g. 1.0.0)."
            )

    @property
    def archive_name(self) -> str:
        return f"{self.name}-{self.version}"


def load_manifest(directory: Path | None = None) -> NoriManifest:
    """Load and validate nori.toml from the given directory (default: cwd)."""
    if directory is None:
        directory = Path.cwd()
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.exists():
        raise ManifestError(f"No {MANIFEST_NAME} found in {directory}")
    with open(manifest_path, "rb") as f:
        data = tomllib.load(f)
    return _parse_manifest(data)


def load_manifest_from_string(text: str) -> NoriManifest:
    """Load manifest from a TOML string (for reading from archives)."""
    data = tomllib.loads(text)
    return _parse_manifest(data)


def _parse_manifest(data: dict) -> NoriManifest:
    pkg = data.get("package", {})
    files = data.get("files", {})
    install = data.get("install", {})
    if not pkg.get("name"):
        raise ManifestError("Missing required field: [package] name")
    if not pkg.get("version"):
        raise ManifestError("Missing required field: [package] version")
    manifest = NoriManifest(
        name=pkg["name"],
        version=pkg["version"],
        description=pkg.get("description", ""),
        author=pkg.get("author", ""),
        license=pkg.get("license", ""),
        libraries=files.get("libraries", []),
        executables=files.get("executables", []),
        data=files.get("data", []),
        source=install.get("source", ""),
    )
    manifest.validate()
    return manifest
