"""Nori package manager constants."""
from sushi_lang.internals.user_cache import sushi_home, user_cache_root

SUSHI_HOME = sushi_home()
BIN_DIR = SUSHI_HOME / "bin"
CACHE_DIR = user_cache_root()
BENTO_DIR = SUSHI_HOME / "bento"
STORE_DIR = SUSHI_HOME / "store"

LOCAL_DEPS_DIR = ".sushi_bento"

MANIFEST_NAME = "nori.toml"
ARCHIVE_EXT = ".nori"

DEFAULT_REPOSITORY = "omakase.lubica.net"
REPOSITORY_ENV_VAR = "SUSHI_REPOSITORY"
