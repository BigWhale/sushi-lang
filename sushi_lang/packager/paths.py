"""Path helpers for ~/.sushi/ directory structure."""
from pathlib import Path

from sushi_lang.packager.constants import SUSHI_HOME, BIN_DIR, CACHE_DIR, BENTO_DIR


def ensure_sushi_home() -> None:
    """Create the ~/.sushi/ directory structure if it doesn't exist."""
    for d in (SUSHI_HOME, BIN_DIR, CACHE_DIR, BENTO_DIR):
        d.mkdir(parents=True, exist_ok=True)


def package_dir(name: str) -> Path:
    return BENTO_DIR / name


def package_lib_dir(name: str) -> Path:
    return BENTO_DIR / name / "lib"


def package_bin_dir(name: str) -> Path:
    return BENTO_DIR / name / "bin"


def package_data_dir(name: str) -> Path:
    return BENTO_DIR / name / "data"
