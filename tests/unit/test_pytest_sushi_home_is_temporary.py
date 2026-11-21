"""The pytest layer never writes the per-user tree of the developer, `~/.sushi`."""
from __future__ import annotations

from pathlib import Path

from sushi_lang.compiler.cache import CacheManager
from sushi_lang.internals.user_cache import sushi_home


def test_sushi_home_is_not_the_real_home():
    assert sushi_home() != Path.home() / ".sushi"


def test_a_stdlib_object_goes_under_the_temporary_home(tmp_path):
    cache = CacheManager(tmp_path, opt_level="mem2reg")
    path = cache.stdlib_object_path("io/stdio", "fp-1")
    assert not path.is_relative_to(Path.home() / ".sushi")
