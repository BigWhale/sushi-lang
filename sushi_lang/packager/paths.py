"""Path helpers for ~/.sushi/ directory structure."""
import os
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


def store_package_dir(name: str, version: str) -> Path:
    return STORE_DIR / f"{name}-{version}"


def find_project_root() -> Path | None:
    """The working directory when it holds a nori.toml, else None.

    There is no walk up to a parent directory. A nori.toml that exists in any form (a
    broken link, a directory) counts, so that a reader refuses it instead of skipping it.
    """
    root = Path.cwd()
    return root if os.path.lexists(root / MANIFEST_NAME) else None


def project_deps_dir(project_root: Path) -> Path:
    return project_root / LOCAL_DEPS_DIR
