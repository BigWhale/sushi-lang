"""The per-user home of Sushi, `~/.sushi`, and the cache root of the compiler in it.

`SUSHI_HOME` moves the tree. A cache directory is used only when it is a directory of
the user that no other user can write, because a cache file can be a Python pickle.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional

SUSHI_HOME_ENV = "SUSHI_HOME"


def sushi_home() -> Path:
    """`$SUSHI_HOME` when it is an absolute path, else `~/.sushi`."""
    override = os.environ.get(SUSHI_HOME_ENV)
    if override and os.path.isabs(override):
        return Path(override)
    return Path.home() / ".sushi"


def user_cache_root() -> Path:
    """`~/.sushi/cache`: the packager and the compiler keep their caches here."""
    return sushi_home() / "cache"


def compiler_cache_root() -> Optional[Path]:
    """`~/.sushi/cache/compiler`, or None when there is no home directory."""
    try:
        return user_cache_root() / "compiler"
    except (RuntimeError, KeyError, OSError):
        return None


def owned_by_me(st: os.stat_result) -> bool:
    """The entry is mine, and no other user can write it."""
    return st.st_uid == os.geteuid() and not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def trusted_dir(cache_dir: Path) -> bool:
    """Make the directory if it is missing; True only if it is mine alone."""
    if not hasattr(os, "geteuid"):
        return False
    try:
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = os.lstat(cache_dir)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and owned_by_me(st)


def compiler_cache_dir(name: str) -> Optional[Path]:
    """`~/.sushi/cache/compiler/<name>`, made and trusted, or None."""
    root = compiler_cache_root()
    if root is None or not trusted_dir(root / name):
        return None
    return root / name
