"""Nori manifest (nori.toml) loading and validation."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from sushi_lang.packager.constants import MANIFEST_NAME
from sushi_lang.packager.errors import NoriError, toml_error

# Package name: lowercase alphanumeric + hyphens, 1-64 chars
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9\-]{0,63}$")

# Version: major.minor.patch
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class ManifestError(NoriError):
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
    dependencies: dict[str, str] = field(default_factory=dict)
    source: str = ""

    def validate(self) -> None:
        if not NAME_PATTERN.match(self.name):
            raise ManifestError("NE1005", name=self.name)
        if not VERSION_PATTERN.match(self.version):
            raise ManifestError("NE1006", version=self.version)

    @property
    def archive_name(self) -> str:
        return f"{self.name}-{self.version}"


def load_manifest(directory: Path | None = None) -> NoriManifest:
    """Load and validate nori.toml from the given directory (default: cwd)."""
    if directory is None:
        directory = Path.cwd()
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.exists():
        raise ManifestError("NE1001", manifest=MANIFEST_NAME, directory=directory)
    return load_manifest_from_string(manifest_path.read_bytes(), str(manifest_path))


def load_manifest_from_string(text: str | bytes, origin: str = MANIFEST_NAME) -> NoriManifest:
    """Load a manifest from TOML text; every error names `origin`."""
    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        return _parse_manifest(tomllib.loads(text))
    except tomllib.TOMLDecodeError as e:
        raise toml_error(origin, e).recast(ManifestError) from e
    except UnicodeDecodeError as e:
        raise ManifestError("NE1010", path=origin) from e
    except ManifestError as e:
        raise e.recast(ManifestError, path=origin) from e


def _table(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ManifestError("NE1003", table=key, kind=type(value).__name__)
    return value


def _parse_manifest(data: dict) -> NoriManifest:
    pkg = _table(data, "package")
    files = _table(data, "files")
    install = _table(data, "install")
    deps = _table(data, "dependencies")
    if not pkg.get("name"):
        raise ManifestError("NE1004", field="name")
    if not pkg.get("version"):
        raise ManifestError("NE1004", field="version")

    # Validate dependency versions
    for dep_name, dep_version in deps.items():
        if not isinstance(dep_version, str):
            raise ManifestError("NE1007", name=dep_name)
        if not VERSION_PATTERN.match(dep_version):
            raise ManifestError("NE1008", version=dep_version, name=dep_name)

    manifest = NoriManifest(
        name=pkg["name"],
        version=pkg["version"],
        description=pkg.get("description", ""),
        author=pkg.get("author", ""),
        license=pkg.get("license", ""),
        libraries=files.get("libraries", []),
        executables=files.get("executables", []),
        data=files.get("data", []),
        dependencies=deps,
        source=install.get("source", ""),
    )
    manifest.validate()
    return manifest
