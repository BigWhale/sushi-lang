"""The grammar-table cache of `sushi_lang/internals/parser.py` (#1046).

The tests use a small grammar that is not Sushi, and no test parses: a parse would be a
compiler stage. A cache never changes an answer, so a broken or a foreign cache file is
rebuilt or ignored, and the tables that come back are the tables a plain build makes.
"""
from __future__ import annotations

import os
from pathlib import Path

from sushi_lang.internals import parser as sushi_parser

GRAMMAR = """
?start: sum
sum: NUMBER ("+" NUMBER)*
%import common.NUMBER
%import common.WS_INLINE
%ignore WS_INLINE
"""

OPTIONS = dict(parser="lalr", lexer="basic", start=["start", "sum"])


def _grammar(tmp_path: Path, text: str = GRAMMAR) -> Path:
    path = tmp_path / "tiny.lark"
    path.write_text(text)
    return path


def _cache_dir(tmp_path: Path) -> Path:
    path = tmp_path / "cache"
    path.mkdir(mode=0o700)
    return path


def _files(cache_dir: Path) -> list[Path]:
    return sorted(p for p in cache_dir.iterdir() if not p.name.startswith("."))


def _rules(lark) -> list[str]:
    return sorted(str(rule) for rule in lark.rules)


def test_the_first_build_writes_one_cache_file(tmp_path: Path) -> None:
    grammar, cache_dir = _grammar(tmp_path), _cache_dir(tmp_path)
    built = sushi_parser.cached_lark(grammar, cache_dir, **OPTIONS)
    files = _files(cache_dir)
    assert len(files) == 1
    assert list(cache_dir.iterdir()) == files, "a temporary file stays behind"
    assert _rules(built) == _rules(sushi_parser.cached_lark(grammar, None, **OPTIONS))


def test_the_second_call_loads_and_does_not_build(tmp_path: Path, monkeypatch) -> None:
    grammar, cache_dir = _grammar(tmp_path), _cache_dir(tmp_path)
    first = sushi_parser.cached_lark(grammar, cache_dir, **OPTIONS)
    before = _files(cache_dir)[0].read_bytes()

    def refuse(*_args, **_kwargs):
        raise AssertionError("the cache file was not used")

    monkeypatch.setattr(sushi_parser, "_build_lark", refuse)
    loaded = sushi_parser.cached_lark(grammar, cache_dir, **OPTIONS)
    assert _rules(loaded) == _rules(first)
    assert _files(cache_dir)[0].read_bytes() == before


def test_a_truncated_cache_file_is_rebuilt(tmp_path: Path) -> None:
    grammar, cache_dir = _grammar(tmp_path), _cache_dir(tmp_path)
    good = sushi_parser.cached_lark(grammar, cache_dir, **OPTIONS)
    cache_file = _files(cache_dir)[0]
    whole = cache_file.read_bytes()
    cache_file.write_bytes(whole[: len(whole) // 3])

    again = sushi_parser.cached_lark(grammar, cache_dir, **OPTIONS)
    assert _rules(again) == _rules(good)
    rebuilt = sushi_parser._load_cached(cache_file, {})
    assert rebuilt is not None, "the truncated file was not written again"
    assert _rules(rebuilt) == _rules(good)


def test_a_changed_grammar_does_not_read_the_old_file(tmp_path: Path) -> None:
    cache_dir = _cache_dir(tmp_path)
    sushi_parser.cached_lark(_grammar(tmp_path), cache_dir, **OPTIONS)
    changed = GRAMMAR.replace('"+"', '"-"')
    lark = sushi_parser.cached_lark(_grammar(tmp_path, changed), cache_dir, **OPTIONS)
    assert len(_files(cache_dir)) == 2
    assert "MINUS" in {t.name for t in lark.terminals}


def test_a_directory_others_can_write_is_not_used(tmp_path: Path) -> None:
    grammar, cache_dir = _grammar(tmp_path), _cache_dir(tmp_path)
    os.chmod(cache_dir, 0o777)
    sushi_parser.cached_lark(grammar, cache_dir, **OPTIONS)
    assert _files(cache_dir) == []


def test_a_cache_file_others_can_write_is_not_loaded(tmp_path: Path, monkeypatch) -> None:
    grammar, cache_dir = _grammar(tmp_path), _cache_dir(tmp_path)
    sushi_parser.cached_lark(grammar, cache_dir, **OPTIONS)
    os.chmod(_files(cache_dir)[0], 0o666)
    built: list[bool] = []
    original = sushi_parser._build_lark

    def counting(*args, **kwargs):
        built.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(sushi_parser, "_build_lark", counting)
    sushi_parser.cached_lark(grammar, cache_dir, **OPTIONS)
    assert built == [True]


def test_a_directory_that_cannot_be_made_falls_back(tmp_path: Path) -> None:
    grammar = _grammar(tmp_path)
    blocker = tmp_path / "file"
    blocker.write_text("")
    lark = sushi_parser.cached_lark(grammar, blocker / "cache", **OPTIONS)
    assert _rules(lark) == _rules(sushi_parser.cached_lark(grammar, None, **OPTIONS))


def test_the_variable_can_turn_the_cache_off(monkeypatch) -> None:
    monkeypatch.setenv("SUSHI_GRAMMAR_CACHE_DIR", "off")
    assert sushi_parser.grammar_cache_dir() is None


def test_the_variable_names_the_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUSHI_GRAMMAR_CACHE_DIR", str(tmp_path))
    assert sushi_parser.grammar_cache_dir() == tmp_path
