"""The stdlib names every `dont_panic` function it carries (#1247, D14).

A text scan of `sushi_lang/sushi_stdlib/src_sushi/`: each function whose header carries
the marker must be listed in MARKED with its reason and the benchmark that justified it.
A new marked function fails CI until it is named, and an entry that matches no marked
function fails too. The scan reads text and never starts the compiler.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from test_metadata import corpus_files, corpus_text  # noqa: E402

STDLIB_SOURCES = ROOT / "sushi_lang" / "sushi_stdlib" / "src_sushi"

#: (path under src_sushi/, function name, reason, benchmark)
MARKED: tuple[tuple[str, str, str, str], ...] = ()

_MARKER = re.compile(r'\s+dont_panic\s+because\s+"(?:[^"\\]|\\.)*"\s*:?\s*$')
_WORD = re.compile(r"\bdont_panic\b")
_FREE = re.compile(r"^\s*(?:public\s+)?(?:static\s+)?fn\s+(?P<name>\w+)")
_CONVERSION = re.compile(r"^\s*extend\s+(?P<name>.+?\s+as\s+.+?)\s*$")
_EXTEND = re.compile(
    r"^\s*extend\s+(?P<target>.+?)\s+(?:static\s+)?(?P<name>\w+)(?:@\([^)]*\))?\("
)


def logical_lines(text: str) -> list[tuple[int, str, str]]:
    """(first line, code, masked code) for each logical line of `text`.

    Comments and doc blocks are removed, a newline inside parentheses joins the lines,
    and `masked` blanks the inside of every string.
    """
    out: list[tuple[int, str, str]] = []
    code: list[str] = []
    masked: list[str] = []
    depth = 0
    line = start = 1
    quote = ""
    i, n = 0, len(text)

    def flush() -> None:
        out.append((start, "".join(code), "".join(masked)))
        code.clear()
        masked.clear()

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            quote = ""
            if depth > 0:
                code.append(" ")
                masked.append(" ")
            else:
                flush()
                start = line
            i += 1
        elif quote:
            code.append(c)
            masked.append(" ")
            if c == "\\" and i + 1 < n and text[i + 1] != "\n":
                code.append(text[i + 1])
                masked.append(" ")
                i += 1
            elif c == quote:
                quote = ""
            i += 1
        elif text.startswith("##:", i):
            end = text.find(":##", i + 3)
            end = n if end < 0 else end + 3
            line += text.count("\n", i, end)
            i = end
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        else:
            if c in "\"'":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth = max(0, depth - 1)
            code.append(c)
            masked.append(c)
            i += 1
    flush()
    return out


def scan_text(text: str) -> tuple[list[tuple[int, str]], list[int]]:
    """The (line, name) of each nameable marker, and the lines of markers that are not."""
    named: list[tuple[int, str]] = []
    unnamed: list[int] = []
    for first, code, masked in logical_lines(text):
        words = len(_WORD.findall(masked))
        if not words:
            continue
        header = _MARKER.sub("", code) if words == 1 and _MARKER.search(code) else None
        name = None
        if header is not None:
            for pattern in (_FREE, _CONVERSION, _EXTEND):
                found = pattern.match(header)
                if found:
                    if pattern is _EXTEND:
                        name = f"{found.group('target')} {found.group('name')}"
                    else:
                        name = found.group("name")
                    break
        if name is None:
            unnamed.append(first)
        else:
            named.append((first, name))
    return named, unnamed


def scan() -> tuple[Counter[tuple[str, str]], list[str]]:
    found: Counter[tuple[str, str]] = Counter()
    problems: list[str] = []
    for path in corpus_files(STDLIB_SOURCES, "*.sushi"):
        rel = path.relative_to(STDLIB_SOURCES).as_posix()
        named, unnamed = scan_text(corpus_text(path))
        for _line, name in named:
            found[(rel, name)] += 1
        problems += [f"{rel}:{line}: marker not nameable" for line in unnamed]
    return found, problems


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
    named, unnamed = scan_text(sample)
    assert [name for _line, name in named] == [
        "first", "second", "string byte_at", "List@(T) pick", "get", "A as B",
    ]
    assert unnamed == []


def test_scanner_handles_the_hard_shapes() -> None:
    sample = (
        "fn split(i32 a,\n        i32 b) i32 dont_panic because \"x\":\n"
        "    return a\n"
        'fn noted() i32 dont_panic because "x":  # note\n'
        "    return 1\n"
        'fn quoted() i32 dont_panic because "say \\"hi\\"":\n'
        "    return 1\n"
        "extend Box with Pk:\n"
        '    static fn make() i32 dont_panic because "x":\n'
        "        return 1\n"
        '    fn twin() i32 dont_panic because "x":\n'
        "        return 1\n"
        "extend Other with Pk:\n"
        '    fn twin() i32 dont_panic because "x":\n'
        "        return 1\n"
        "##:\n"
        'fn documented() i32 dont_panic because "x":\n'
        ":##\n"
        'let string s = "dont_panic"\n'
    )
    named, unnamed = scan_text(sample)
    assert [name for _line, name in named] == [
        "split", "noted", "quoted", "make", "twin", "twin",
    ]
    assert unnamed == []


def test_scanner_fails_closed_on_a_marker_it_cannot_name() -> None:
    named, unnamed = scan_text('let i32 x = dont_panic\nfn f() i32 dont_panic:\n    return 1\n')
    assert named == []
    assert unnamed == [1, 2]


def test_every_marker_is_nameable() -> None:
    assert not scan()[1]


def test_marked_functions_are_named() -> None:
    named = Counter((path, name) for path, name, _reason, _bench in MARKED)
    missing = sorted((scan()[0] - named).elements())
    assert not missing, f"marked stdlib functions not named in MARKED: {missing}"


def test_every_entry_matches_a_marked_function() -> None:
    named = Counter((path, name) for path, name, _reason, _bench in MARKED)
    stale = sorted((named - scan()[0]).elements())
    assert not stale, f"MARKED entries that match no marked function: {stale}"


def test_every_entry_has_a_reason_and_a_benchmark() -> None:
    bad = [(path, name) for path, name, reason, bench in MARKED
           if not reason.strip() or not bench.strip()]
    assert not bad, f"MARKED entries with no reason or no benchmark: {bad}"
