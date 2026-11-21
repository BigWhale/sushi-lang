"""The per-user cache root of the compiler, `sushi_lang/internals/user_cache.py`.

No test parses: the tests read paths and directory modes only.
"""
from __future__ import annotations

import os
from pathlib import Path

from sushi_lang.internals import parser as sushi_parser
from sushi_lang.internals import user_cache
from sushi_lang.packager import constants


def test_the_root_is_under_the_home_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SUSHI_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert user_cache.sushi_home() == tmp_path / ".sushi"
    assert user_cache.compiler_cache_root() == tmp_path / ".sushi" / "cache" / "compiler"


def test_sushi_home_moves_the_tree(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUSHI_HOME", str(tmp_path / "elsewhere"))
    assert user_cache.compiler_cache_root() == tmp_path / "elsewhere" / "cache" / "compiler"


def test_a_relative_sushi_home_is_not_used(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUSHI_HOME", "relative")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert user_cache.sushi_home() == tmp_path / ".sushi"


def test_the_packager_and_the_compiler_share_the_home(monkeypatch) -> None:
    monkeypatch.delenv("SUSHI_HOME", raising=False)
    assert constants.SUSHI_HOME == user_cache.sushi_home()
    assert constants.CACHE_DIR == user_cache.compiler_cache_root().parent


def test_the_grammar_tables_go_under_the_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SUSHI_GRAMMAR_CACHE_DIR", raising=False)
    monkeypatch.setenv("SUSHI_HOME", str(tmp_path))
    assert sushi_parser.grammar_cache_dir() == tmp_path / "cache" / "compiler" / "grammar"


def test_a_cache_dir_is_made_for_the_user_only(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUSHI_HOME", str(tmp_path / "home"))
    made = user_cache.compiler_cache_dir("stdlib")
    assert made == tmp_path / "home" / "cache" / "compiler" / "stdlib"
    assert made.is_dir()
    assert os.stat(made).st_mode & 0o777 == 0o700


def test_a_cache_dir_others_can_write_is_refused(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUSHI_HOME", str(tmp_path))
    target = tmp_path / "cache" / "compiler" / "stdlib"
    target.mkdir(parents=True)
    os.chmod(target, 0o777)
    assert user_cache.compiler_cache_dir("stdlib") is None


def test_a_cache_dir_that_cannot_be_made_is_refused(monkeypatch, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("SUSHI_HOME", str(blocker))
    assert user_cache.compiler_cache_dir("stdlib") is None
