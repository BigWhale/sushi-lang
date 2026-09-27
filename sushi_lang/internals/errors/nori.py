"""Nori package manager errors (NExxxx).

The NE family is the packager's; CE stays the compiler's. The ranges: NE00xx internal,
NE10xx the manifest, NE20xx the archive, NE30xx the installed packages, NE40xx the
operating system, NE50xx the package repository. `sushi_lang/packager/errors.py` raises
and renders them.
"""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


def _nori(code: str, text: str, doc: str, kind: Category = Category.PACKAGER) -> None:
    _add(ErrorMessage(code, Severity.ERROR, text, kind, doc))


# Internal (NE00xx)
_nori("NE0000", "internal error in nori: {detail}",
      "A fault in nori itself, not in the package or in the environment. It exits 2, as "
      "the compiler's CE0000 does, and `--traceback` appends the Python traceback.",
      Category.INTERNAL)

# The manifest (NE10xx)
_nori("NE1001", "no {manifest} found in '{directory}'",
      "A command that works on a package reads its manifest from the current directory.")
_nori("NE1002", "'{path}' is not valid TOML: {detail}",
      "A TOML file that nori reads could not be parsed: the project manifest, the manifest "
      "of an installed package, or the credentials file. The text names the line and the "
      "column.")
_nori("NE1003", "'{path}': [{table}] must be a table, not {kind}",
      "Each manifest section is a TOML table.")
_nori("NE1004", "'{path}': missing required field: [package] {field}",
      "A manifest must state the package name and its version.")
_nori("NE1005",
      "'{path}': invalid package name '{name}': use 1 to 64 lowercase letters, digits and "
      "hyphens, and start with a letter",
      "A package name is also a directory name and a part of a repository URL.")
_nori("NE1006",
      "'{path}': invalid version '{version}': use the form major.minor.patch (for example 1.0.0)",
      "A package version has three numbers.")
_nori("NE1007", "'{path}': the version of dependency '{name}' must be a string",
      "A dependency is written `name = \"1.0.0\"`.")
_nori("NE1008",
      "'{path}': invalid version '{version}' for dependency '{name}': use the form "
      "major.minor.patch (for example 1.0.0)",
      "A dependency names one exact version.")
_nori("NE1009", "'{path}' already exists",
      "`nori init` does not write over a manifest.")
_nori("NE1010", "'{path}' is not UTF-8 text",
      "A manifest is TOML, and TOML is UTF-8.")

# The archive (NE20xx)
_nori("NE2001", "'{path}' is not a readable .nori archive: {reason}",
      "A .nori archive is a gzip-compressed tar file.")
_nori("NE2002", "'{path}' holds no package directory '{directory}/'",
      "An archive holds one directory, `<name>-<version>/`, named by its manifest.")
_nori("NE2003", "'{path}': cannot read '{member}'",
      "The archive member that holds the manifest is not a regular file.")
_nori("NE2004", "'{path}' holds no {manifest}",
      "Every archive carries its manifest.")
_nori("NE2005", "listed path not found: '{path}'",
      "The [files] section of the manifest names a path that does not exist.")
_nori("NE2006", "listed path is not a file: '{path}'",
      "A library or an executable in the [files] section must be a regular file.")
_nori("NE2007", "archive not found: '{path}'",
      "The .nori archive to install or to publish does not exist.")

# Installed packages (NE30xx)
_nori("NE3001", "package {name} v{version} is not in the store",
      "A project links a package version that is in the global store.")
_nori("NE3002", "dependencies missing from the store: {packages}",
      "`nori install` in a project links each dependency from the store; it does not fetch one.")
_nori("NE3003", "package '{name}' is not installed",
      "The package is not in the global package directory.")
_nori("NE3004", "package '{name}' is not a project dependency",
      "The package is not linked in the project's .sushi_bento directory.")
_nori("NE3005", "path not found: '{path}'",
      "The source of an install does not exist.")
_nori("NE3006", "cannot install from '{source}': it is not a .nori archive or a directory",
      "An install source is a .nori archive, or a directory that holds one or a manifest.")
_nori("NE3007", "no .nori archive for '{package}' in '{directory}'",
      "The directory holds no `<package>-*.nori` archive and no manifest.")
_nori("NE3008", "not in a Sushi project (no {manifest} found)",
      "A bare `nori install` restores the dependencies of the project it runs in.")
_nori("NE3009", "remote install from {repository} is not implemented yet",
      "Install from a local source until the repository install exists.")
_nori("NE3010", "package '{name}' has a corrupted manifest",
      "The installed package is present, but nori cannot read its manifest.")
_nori("NE3011", "expected 'from' before the source, found '{found}'",
      "The form is `nori install <package> from <source>`.")

# The operating system (NE40xx)
_nori("NE4001", "cannot use '{path}': {reason}",
      "The operating system refused an operation on a path: no permission, a full disk, a "
      "directory where a file must be. It is the environment's fault, not nori's, so it "
      "exits 1 and names the path and the system reason.")
_nori("NE4002", "a system operation failed: {reason}",
      "The operating system refused an operation that names no path.")

# The package repository (NE50xx)
_nori("NE5001", "cannot connect to {repository}: {reason}",
      "The repository did not answer: no network, a wrong host name, or a timeout.")
_nori("NE5002", "the repository answered HTTP {status}: {detail}",
      "The repository refused the request with a status that has no code of its own.")
_nori("NE5003", "not logged in to {repository}",
      "`nori publish` needs the API token that `nori login` stores.")
_nori("NE5004", "{repository} refused the API key: it is invalid, expired or revoked",
      "The repository answered HTTP 401.")
_nori("NE5005", "invalid API key format: a key starts with '{prefix}'",
      "nori checks the form of a key before it sends the key to the repository.")
_nori("NE5006", "permission denied: you are not the owner of '{name}'",
      "The repository answered HTTP 403: only the owner publishes a new version.")
_nori("NE5007", "version {version} of '{name}' is already published",
      "The repository answered HTTP 409: a published version does not change.")
_nori("NE5008", "the archive is larger than the size limit of the repository (50 MB)",
      "The repository answered HTTP 413.")
_nori("NE5009", "the repository refused the package: {detail}",
      "The repository answered HTTP 422: the manifest or the archive failed its checks.")
