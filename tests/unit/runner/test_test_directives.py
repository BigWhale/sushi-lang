"""Every directive-shaped header line in the corpus is one the runner actually reads."""
from __future__ import annotations

import pytest

from _harness import TESTS_DIR
from test_metadata import (
    _CODE_PREFIX,
    _DIRECTIVE_NAME,
    FLAG_DIRECTIVES,
    VALUED_DIRECTIVES,
    corpus_files,
    corpus_text,
    header_block,
)


# The known names are the parser's own two tables: a name is known when it has a row.
PARSER_NAMES = frozenset(VALUED_DIRECTIVES) | frozenset(FLAG_DIRECTIVES)


def _sushi_tests():
    return corpus_files(TESTS_DIR)


def _directive_name(comment: str) -> str | None:
    """`EXPECT_NO_LEAKS: true` -> `EXPECT_NO_LEAKS`; None for a line that is prose."""
    match = _DIRECTIVE_NAME.match(comment)
    if match is None or _CODE_PREFIX.match(comment):
        return None
    return match.group()


def test_corpus_is_not_empty():
    """A rglob that silently matched nothing would make every check below vacuous."""
    assert len(_sushi_tests()) > 1000


def test_every_directive_is_inside_the_parsed_header():
    """A directive below the leading comment block is never read."""
    stranded = []
    for path in _sushi_tests():
        lines = corpus_text(path).split("\n")
        header_len = len(header_block(lines))
        for lineno, line in enumerate(lines[header_len:], start=header_len + 1):
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            comment = stripped[1:].strip()
            if _directive_name(comment) in PARSER_NAMES:
                rel = path.relative_to(TESTS_DIR)
                stranded.append(f"{rel}:{lineno}: {comment}")

    assert not stranded, (
        "these directives sit after the file's leading comment block, so the runner "
        "never reads them and the test asserts nothing:\n  " + "\n  ".join(stranded))


def test_every_directive_name_is_one_the_parser_knows():
    """A misspelled directive is discarded in silence, so it must not exist."""
    unknown = []
    for path in _sushi_tests():
        lines = corpus_text(path).split("\n")
        for lineno, line in enumerate(header_block(lines), start=1):
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            name = _directive_name(stripped[1:].strip())
            if name is not None and name not in PARSER_NAMES:
                rel = path.relative_to(TESTS_DIR)
                unknown.append(f"{rel}:{lineno}: {name}")

    assert not unknown, (
        "these directive names match no branch of parse_test_metadata, so they assert "
        f"nothing. Known names: {sorted(PARSER_NAMES)}\n  " + "\n  ".join(unknown))


@pytest.mark.parametrize("name", ["test_own_get_copy_at_call", "test_chained_clone_on_getout"])
def test_the_leak_gated_memory_tests_really_are_gated(name):
    """Spot-check the parser end to end on files that exist to prove an absence of leaks."""
    from test_metadata import parse_test_metadata

    path = TESTS_DIR / "memory" / f"{name}.sushi"
    assert path.is_file(), f"{path} moved; update this test or the name"
    assert parse_test_metadata(path).expect_no_leaks


def test_no_corpus_file_makes_the_parser_warn(capsys):
    """A warning means the runner discarded something the file meant to assert.

    The value gate to `test_every_directive_name_is_one_the_parser_knows`'s name gate:
    `TIMEOUT_SECONDS: soon` names a real directive and still asserts nothing, because
    the parser accepts only an integer there. The warning is also what
    breaks the badge job -- `--json` stdout is piped straight into `corpus-results.json`.
    """
    from test_metadata import parse_test_metadata

    for path in _sushi_tests():
        parse_test_metadata(path)

    captured = capsys.readouterr()
    warnings = [
        line for line in (captured.out + captured.err).split("\n")
        if line.startswith("Warning:")
    ]
    assert not warnings, (
        "parsing the corpus emitted warnings, so these files assert less than they "
        "spell:\n  " + "\n  ".join(warnings))


def test_no_corpus_file_has_a_directive_block_the_runner_cannot_read():
    """A directive block that is not UTF-8 fails its fixture; none may be in the corpus."""
    from test_metadata import parse_test_metadata

    unreadable = [f"{path.relative_to(TESTS_DIR)}: {'; '.join(errors)}"
                  for path in _sushi_tests()
                  if (errors := parse_test_metadata(path).directive_errors)]
    assert not unreadable, "\n  ".join(["directive errors:"] + unreadable)
