"""Path helpers for ~/.sushi/ directory structure."""
from pathlib import Path

from sushi_lang.packager.constants import (
    SUSHI_HOME, BIN_DIR, CACHE_DIR, BENTO_DIR, STORE_DIR,
    MANIFEST_NAME, LOCAL_DEPS_DIR,
)


def ensure_sushi_home() -> None:
    """Create the ~/.sushi/ directory structure if it doesn't exist."""
    for d in (SUSHI_HOME, BIN_DIR, CACHE_DIR, BENTO_DIR, STORE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def package_dir(name: str) -> Path:
    return BENTO_DIR / name


def package_lib_dir(name: str) -> Path:
    return BENTO_DIR / name / "lib"


def package_bin_dir(name: str) -> Path:
    return BENTO_DIR / name / "bin"


def package_data_dir(name: str) -> Path:
    return BENTO_DIR / name / "data"


def store_package_dir(name: str, version: str) -> Path:
    return STORE_DIR / f"{name}-{version}"


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from start (default: cwd) looking for a nori.toml with [dependencies].

    Returns the directory containing the manifest, or None if not found.
    """
    if start is None:
        start = Path.cwd()
    current = start.resolve()
    while True:
        manifest_path = current / MANIFEST_NAME
        if manifest_path.is_file():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def project_deps_dir(project_root: Path) -> Path:
    return project_root / LOCAL_DEPS_DIR
