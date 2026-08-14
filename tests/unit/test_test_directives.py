"""Every `# EXPECT_*` directive in the corpus is one the runner actually reads.

A directive the parser does not reach does not fail -- the test stops asserting and the
suite still reports it as passed. That is the failure mode this project spends the most
effort on elsewhere: CLAUDE.md is explicit that "all tests passed" must never be mistaken
for "leaks were checked", and the runner prints `Leak checks: N` for exactly that reason.
The directives themselves had no such guard.

Two ways a directive goes dead, both found in the corpus rather than imagined:

1. **Out of the parsed region.** `parse_test_metadata` read a fixed first-20-lines window,
   so any test with a header long enough to explain itself lost whatever came after. 23
   directives over six files, including four `EXPECT_NO_LEAKS` on memory tests -- among
   them three of the four files whose whole purpose is to prove a chained `.clone()` does
   not leak. The window is now the leading comment block, and this test pins that a
   directive lives there.

2. **Misspelled.** `# EXPECT_STDOUT: 4` matches no branch of the parser and is discarded in
   silence. Four stdlib string tests carried seven of them; every one was a stdout
   assertion that never ran.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS_ROOT))

from test_metadata import header_block  # noqa: E402


# Every directive prefix `parse_test_metadata` dispatches on. Kept as a literal rather
# than scraped from the parser: a typo in the parser should fail this test, not be
# mirrored by it.
KNOWN_DIRECTIVES = frozenset({
    "EXPECT_RUNTIME_EXIT",
    "EXPECT_STDOUT_CONTAINS",
    "EXPECT_STDOUT_EXACT",
    "EXPECT_STDERR_CONTAINS",
    "EXPECT_STDERR_EMPTY",
    "EXPECT_NO_LEAKS",
    "EXPECT_ERROR_CODE",
})


def _sushi_tests():
    return sorted(TESTS_ROOT.rglob("*.sushi"))


def _directive_name(comment: str) -> str:
    """`EXPECT_NO_LEAKS: true` -> `EXPECT_NO_LEAKS`."""
    return comment.split(":", 1)[0].strip()


def test_corpus_is_not_empty():
    """A rglob that silently matched nothing would make every check below vacuous."""
    assert len(_sushi_tests()) > 1000


def test_every_directive_is_inside_the_parsed_header():
    """A directive below the leading comment block is never read."""
    stranded = []
    for path in _sushi_tests():
        lines = path.read_text(encoding="utf-8").split("\n")
        header_len = len(header_block(lines))
        for lineno, line in enumerate(lines[header_len:], start=header_len + 1):
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            comment = stripped[1:].strip()
            if comment.startswith("EXPECT_"):
                rel = path.relative_to(TESTS_ROOT)
                stranded.append(f"{rel}:{lineno}: {comment}")

    assert not stranded, (
        "these directives sit after the file's leading comment block, so the runner "
        "never reads them and the test asserts nothing:\n  " + "\n  ".join(stranded))


def test_every_directive_name_is_one_the_parser_knows():
    """A misspelled directive is discarded in silence, so it must not exist."""
    unknown = []
    for path in _sushi_tests():
        lines = path.read_text(encoding="utf-8").split("\n")
        for lineno, line in enumerate(header_block(lines), start=1):
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            comment = stripped[1:].strip()
            if not comment.startswith("EXPECT_"):
                continue
            name = _directive_name(comment)
            if name not in KNOWN_DIRECTIVES:
                rel = path.relative_to(TESTS_ROOT)
                unknown.append(f"{rel}:{lineno}: {name}")

    assert not unknown, (
        "these directive names match no branch of parse_test_metadata, so they assert "
        f"nothing. Known names: {sorted(KNOWN_DIRECTIVES)}\n  " + "\n  ".join(unknown))


@pytest.mark.parametrize("name", ["test_own_get_copy_at_call", "test_chained_clone_on_getout"])
def test_the_leak_gated_memory_tests_really_are_gated(name):
    """Spot-check the parser end to end on files that exist to prove an absence of leaks.

    `test_own_get_copy_at_call` is the specific file whose `EXPECT_NO_LEAKS` was being
    dropped, which is why it is named here rather than left to the sweep above.
    """
    from test_metadata import parse_test_metadata

    path = TESTS_ROOT / "memory" / f"{name}.sushi"
    assert path.is_file(), f"{path} moved; update this test or the name"
    assert parse_test_metadata(path).expect_no_leaks
