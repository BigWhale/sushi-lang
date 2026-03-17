"""Nori package manager constants."""
from pathlib import Path

SUSHI_HOME = Path.home() / ".sushi"
BIN_DIR = SUSHI_HOME / "bin"
CACHE_DIR = SUSHI_HOME / "cache"
BENTO_DIR = SUSHI_HOME / "bento"

MANIFEST_NAME = "nori.toml"
ARCHIVE_EXT = ".nori"

DEFAULT_REPOSITORY = "omakase.lubica.net"
REPOSITORY_ENV_VAR = "SUSHI_REPOSITORY"
