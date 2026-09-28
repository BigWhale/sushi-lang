"""Nori manifest (nori.toml) loading and validation."""
from __future__ import annotations

import datetime
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


class MalformedManifestError(ManifestError):
    """The file is not UTF-8 TOML, or a table or a field has the wrong TOML type."""


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
        raise toml_error(origin, e).recast(MalformedManifestError) from e
    except UnicodeDecodeError as e:
        raise MalformedManifestError("NE1010", path=origin) from e
    except ManifestError as e:
        raise e.recast(type(e), path=origin) from e


STRING = "a string"
STRING_LIST = "a list of strings"

# The expected TOML type of every manifest field that nori reads. [dependencies] is a
# table of names to versions and has its own codes (NE1007, NE1008).
FIELD_TYPES: dict[tuple[str, str], str] = {
    ("package", "name"): STRING,
    ("package", "version"): STRING,
    ("package", "description"): STRING,
    ("package", "author"): STRING,
    ("package", "license"): STRING,
    ("files", "libraries"): STRING_LIST,
    ("files", "executables"): STRING_LIST,
    ("files", "data"): STRING_LIST,
    ("install", "source"): STRING,
}

# The TOML name of each type that tomllib produces; a datetime is a date, so it goes first.
_TOML_KINDS: tuple[tuple[type, str], ...] = (
    (bool, "a boolean"),
    (str, "a string"),
    (int, "an integer"),
    (float, "a float"),
    (list, "a list"),
    (dict, "a table"),
    (datetime.datetime, "a date-time"),
    (datetime.date, "a date"),
    (datetime.time, "a time"),
)


def _kind(value: object) -> str:
    return next((name for cls, name in _TOML_KINDS if isinstance(value, cls)),
                type(value).__name__)


def _mismatch(value: object, expected: str) -> str | None:
    """The found type when `value` is not `expected`, else None."""
    if expected == STRING:
        return None if isinstance(value, str) else _kind(value)
    if not isinstance(value, list):
        return _kind(value)
    bad = next((item for item in value if not isinstance(item, str)), None)
    return None if bad is None else f"a list that holds {_kind(bad)}"


def _check_field_types(tables: dict[str, dict]) -> None:
    for (table, key), expected in FIELD_TYPES.items():
        if key not in tables[table]:
            continue
        found = _mismatch(tables[table][key], expected)
        if found is not None:
            raise MalformedManifestError("NE1011", field=f"[{table}].{key}", expected=expected, found=found)


def _table(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise MalformedManifestError("NE1003", table=key, kind=type(value).__name__)
    return value


def _parse_manifest(data: dict) -> NoriManifest:
    pkg = _table(data, "package")
    files = _table(data, "files")
    install = _table(data, "install")
    deps = _table(data, "dependencies")
    _check_field_types({"package": pkg, "files": files, "install": install})
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
