"""The stdlib names every `dont_panic` function it carries (#1247, D14).

A text scan of `sushi_lang/sushi_stdlib/src_sushi/`: each function whose header carries
the marker must be listed in MARKED with its reason and the benchmark that justified it.
A new marked function fails CI until it is named, and an entry that matches no marked
function fails too. The scan reads text and never starts the compiler.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from test_metadata import corpus_files, corpus_text  # noqa: E402

STDLIB_SOURCES = ROOT / "sushi_lang" / "sushi_stdlib" / "src_sushi"

#: (path under src_sushi/, function name, reason, benchmark)
MARKED: tuple[tuple[str, str, str, str], ...] = ()

_MARKER = re.compile(r'\s+dont_panic\s+because\s+"[^"]*"\s*:?\s*$')
_FREE = re.compile(r"^\s*(?:public\s+)?fn\s+(?P<name>\w+)")
_CONVERSION = re.compile(r"^\s*extend\s+(?P<name>.+?\s+as\s+.+?)\s*$")
_EXTEND = re.compile(
    r"^\s*extend\s+(?P<target>.+?)\s+(?:static\s+)?(?P<name>\w+)(?:@\([^)]*\))?\("
)


def marked_names(text: str) -> list[str]:
    """The name of every function in `text` whose header carries the marker."""
    names = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0] if "dont_panic" not in line else line
        if not _MARKER.search(stripped):
            continue
        header = _MARKER.sub("", stripped)
        for pattern in (_FREE, _CONVERSION, _EXTEND):
            found = pattern.match(header)
            if found:
                names.append(found.group("name"))
                break
    return names


def scan() -> set[tuple[str, str]]:
    found = set()
    for path in corpus_files(STDLIB_SOURCES, "*.sushi"):
        rel = path.relative_to(STDLIB_SOURCES).as_posix()
        for name in marked_names(corpus_text(path)):
            found.add((rel, name))
    return found


def test_scanner_finds_each_marker_position() -> None:
    sample = (
        'public fn first(string s) bool dont_panic because "i < n holds":\n'
        "    return true\n"
        'fn second@(T)(T[] a) T | StdError dont_panic because "len checked":\n'
        "    return a[0]\n"
        'extend string byte_at(i32 i) u8 dont_panic because "i < size":\n'
        "    return self[i]\n"
        'extend List@(T) static pick@(U)(i32 i) T dont_panic because "ok":\n'
        "    return x\n"
        'extend Box with Pk:\n'
        '    fn get(i32 i) i32 dont_panic because "ok":\n'
        "        return 1\n"
        'extend A as B dont_panic because "ok":\n'
        "    return B.X\n"
        "fn unmarked(string s) bool:\n"
        "    return false\n"
        '# fn commented() bool dont_panic because "x":\n'
    )
    assert marked_names(sample) == [
        "first", "second", "byte_at", "pick", "get", "A as B",
    ]


def test_marked_functions_are_named() -> None:
    named = {(path, name) for path, name, _reason, _bench in MARKED}
    missing = sorted(scan() - named)
    assert not missing, f"marked stdlib functions not named in MARKED: {missing}"


def test_every_entry_matches_a_marked_function() -> None:
    found = scan()
    stale = sorted(
        (path, name) for path, name, _reason, _bench in MARKED if (path, name) not in found
    )
    assert not stale, f"MARKED entries that match no marked function: {stale}"


def test_every_entry_has_a_reason_and_a_benchmark() -> None:
    bad = [(path, name) for path, name, reason, bench in MARKED
           if not reason.strip() or not bench.strip()]
    assert not bad, f"MARKED entries with no reason or no benchmark: {bad}"
