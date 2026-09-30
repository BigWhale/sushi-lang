"""Coverage gate: a success fixture that writes to a stream asserts what it writes.

Two rows, one rule. A success fixture that writes to stdout asserts stdout; a success
fixture that writes to stderr on its normal path asserts stderr. The channel of the
write does not matter: `print`, `println`, a write method on a console handle, and a
console handle handed to a call are all writes.

The scan reads CODE only (comments, doc blocks and string contents removed), and the
assertions come from `parse_test_metadata`, the reader the runner uses.
"""

import re
import sys
from pathlib import Path


# The number of fixtures each row may leave in its gap. Both are 0 and may never rise.
BASELINE = 0
STDERR_BASELINE = 0

TESTS_ROOT = Path(__file__).parent.parent  # tests/

if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from test_metadata import collect_fixtures, corpus_text, parse_test_metadata  # noqa: E402

# Quarantine registries, keyed by path relative to tests/.
#
# Each entry: {"reason": <reason>, "issue": <url or None>}
#   "broken-output" -- the compiler emits WRONG output; the test is a real bug repro.
#                      Requires a tracking issue.
#   "needs-triage"  -- the correct output could not be confidently derived; do NOT
#                      lock in possibly-wrong output. Requires a tracking issue.
#   "no-stdout"     -- the code writes, but the write is on a path a passing run does
#                      not take. A coverage exclusion, not a bug; no issue required.
#
# test_quarantine_registry_valid keeps both honest: every entry names a real fixture
# that still writes on that stream and still has no assertion for it, and every entry
# that is not "no-stdout" carries an issue URL.
QUARANTINE: dict[str, dict] = {
    # (error path taken before print, uncalled printing helper, or empty-collection iteration)
    "array/bounds/test_dynamic_arrays_bounds_runtime.sushi": {"reason": "no-stdout", "issue": None},
    "basic/function_calls/test_function_calls.sushi": {"reason": "no-stdout", "issue": None},
    "list/match_on_get_pop/test_list_match_get.sushi": {"reason": "no-stdout", "issue": None},
    "stdlib/generics/hashmap/test_hashmap_entries_empty.sushi": {"reason": "no-stdout", "issue": None},
    "stdlib/generics/hashmap/test_hashmap_keys_empty.sushi": {"reason": "no-stdout", "issue": None},
}

STDERR_QUARANTINE: dict[str, dict] = {}

_VALID_QUARANTINE_REASONS = {"broken-output", "needs-triage", "no-stdout"}

_PRINT_CALL = re.compile(r"\b(?:print|println)\s*\(")
_WRITE_METHODS = r"(?:write|write_bytes|write_at|writeln|share)"


def _handle_write(handle: str) -> re.Pattern:
    """A write through the console handle `handle`: a write method, or the handle as an argument."""
    return re.compile(
        rf"\b{handle}\s*\.\s*{_WRITE_METHODS}\s*\("
        rf"|[(,]\s*(?:(?:nom|peek|poke)\s+)?{handle}\s*[,)]"
    )


_STDOUT_WRITE = _handle_write("stdout")
_STDERR_WRITE = _handle_write("stderr")

# A match arm whose pattern is a failure: `Result.Err(...) ->`, `Maybe.None ->`.
_FAILURE_ARM = re.compile(r"^\s*[\w.]*\b(?:Err\s*\(.*\)|None)\s*->")
# A block fails when it returns an Err, or, in a bare `main`, a nonzero exit code.
_RETURN_ERR = re.compile(r"^\s*return\s+(?:Result\s*\.\s*Err\s*\(|[1-9]\d*\s*$)")


def code_text(source: str) -> str:
    """`source` with every comment and doc block removed and every string emptied.

    The line structure stays: a removed span keeps its newlines, so the indentation of
    each code line is unchanged.
    """
    out = []
    i, n = 0, len(source)
    while i < n:
        if source.startswith("##:", i):
            end = source.find(":##", i + 3)
            end = n if end < 0 else end + 3
            out.append("\n" * source.count("\n", i, end))
            i = end
        elif source[i] == "#":
            end = source.find("\n", i)
            i = n if end < 0 else end
        elif source[i] in "\"'":
            quote = source[i]
            out.append(quote)
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\":
                    i += 1
                elif source[i] == "\n":
                    out.append("\n")
                i += 1
            out.append(quote)
            i += 1
        else:
            out.append(source[i])
            i += 1
    return "".join(out)


def normal_path_lines(code: str) -> list[str]:
    """The code lines a passing run takes: not in a failure arm, not in a block that fails.

    A failure arm is a match arm on `Result.Err(...)` or `Maybe.None`. A block that fails
    is a run of lines at one indentation that ends in `return Result.Err(...)`, or in a
    nonzero exit code.
    """
    lines = [line for line in code.split("\n") if line.strip()]
    indents = [len(line) - len(line.lstrip()) for line in lines]
    kept = []
    stack: list[tuple[int, bool]] = []
    for i, line in enumerate(lines):
        indent = indents[i]
        while stack and stack[-1][0] >= indent:
            stack.pop()
        failure_arm = bool(_FAILURE_ARM.match(line))
        if not (failure_arm or any(failed for _, failed in stack) or _block_fails(lines, indents, i)):
            kept.append(line)
        stack.append((indent, failure_arm))
    return kept


def _block_fails(lines: list[str], indents: list[int], start: int) -> bool:
    """The block that holds line `start` returns `Result.Err(...)` at its own level."""
    for j in range(start, len(lines)):
        if indents[j] < indents[start]:
            return False
        if indents[j] == indents[start] and _RETURN_ERR.match(lines[j]):
            return True
    return False


def _success_fixtures() -> list[tuple[str, Path]]:
    return [(str(f.relative_to(TESTS_ROOT)), f) for f in collect_fixtures(TESTS_ROOT)
            if not f.name.startswith(("test_err_", "test_warn_"))]


def writes_stdout(code: str) -> bool:
    return bool(_PRINT_CALL.search(code) or _STDOUT_WRITE.search(code))


def writes_stderr(code: str) -> bool:
    return any(_STDERR_WRITE.search(line) for line in normal_path_lines(code))


def asserts_stdout(metadata) -> bool:
    return bool(metadata.expect_stdout_contains) or metadata.expect_stdout_exact is not None


def asserts_stderr(metadata) -> bool:
    return bool(metadata.expect_stderr_contains)


ROWS = {
    "stdout": (writes_stdout, asserts_stdout, QUARANTINE),
    "stderr": (writes_stderr, asserts_stderr, STDERR_QUARANTINE),
}


def _compute_gap(stream: str = "stdout") -> list[str]:
    """The fixtures that write to `stream` and assert nothing about it."""
    writes, asserts, quarantine = ROWS[stream]
    return [rel for rel, f in _success_fixtures()
            if rel not in quarantine
            and writes(code_text(corpus_text(f)))
            and not asserts(parse_test_metadata(f))]


def _assert_gap(stream: str, baseline: int, directives: str) -> None:
    gap_files = _compute_gap(stream)
    gap = len(gap_files)
    assert gap <= baseline, (
        f"{stream} coverage gap ({gap}) exceeds its baseline ({baseline}).\n"
        f"Add {directives} to each fixture below.\n"
        f"\nFiles in gap ({gap}):\n" + "\n".join(f"  {f}" for f in gap_files[:30])
        + (f"\n  ... and {gap - 30} more" if gap > 30 else "")
    )


def test_stdout_coverage_ratchet():
    """A success fixture that writes to stdout asserts stdout."""
    _assert_gap("stdout", BASELINE, "EXPECT_STDOUT_EXACT or EXPECT_STDOUT_CONTAINS")


def test_stderr_coverage_ratchet():
    """A success fixture that writes to stderr on its normal path asserts stderr."""
    _assert_gap("stderr", STDERR_BASELINE, "EXPECT_STDERR_CONTAINS")


def test_quarantine_registry_valid():
    """Keep both quarantine registries honest."""
    problems = []
    for stream, (writes, asserts, quarantine) in ROWS.items():
        for rel, meta in quarantine.items():
            path = TESTS_ROOT / rel
            if not path.is_file():
                problems.append(f"{stream} {rel}: quarantined path does not exist")
                continue
            reason = meta.get("reason")
            if reason not in _VALID_QUARANTINE_REASONS:
                problems.append(
                    f"{stream} {rel}: invalid reason {reason!r} "
                    f"(expected one of {sorted(_VALID_QUARANTINE_REASONS)})"
                )
            if not writes(code_text(corpus_text(path))):
                problems.append(f"{stream} {rel}: writes nothing to {stream} -- remove it")
            if asserts(parse_test_metadata(path)):
                problems.append(f"{stream} {rel}: asserts {stream} -- remove it")
            if reason != "no-stdout" and not meta.get("issue"):
                problems.append(f"{stream} {rel}: reason {reason!r} requires a tracking issue URL")

    assert not problems, "quarantine registry invalid:\n" + "\n".join(f"  {p}" for p in problems)


def test_code_scan_sees_every_write():
    """The scan finds a write in every spelling, and nothing in a comment or a string."""
    source = (
        "# println(\"comment\")\n"
        "##: stderr.write(\"doc\") :##\n"
        "fn main() i32:\n"
        "    let string s = \"stderr.write(x) # println(y)\"\n"
        "    match open(\"f\", FileMode.Read()):\n"
        "        Result.Ok(f) -> f.close()\n"
        "        Result.Err(_) ->\n"
        "            stderr.write(\"failure only\\n\")\n"
        "        Result.Err(_) -> stderr.write(\"failure only\\n\")\n"
        "    if (x):\n"
        "        stderr.write(\"unexpected\\n\")\n"
        "        return Result.Err(StdError.Error)\n"
        "    return Result.Ok(0)\n"
    )
    code = code_text(source)
    assert not writes_stdout(code)
    assert not writes_stderr(code)
    for write in ("stdout.write(\"a\")", "stdout.write_bytes(b)", "emit(poke stdout, \"a\")",
                  "println(1)", "print(1)"):
        assert writes_stdout(code_text(f"fn main() i32:\n    {write}\n")), write
    assert writes_stderr(code_text("fn main() i32:\n    stderr.write(\"a\")\n"))
    assert writes_stderr(code_text(source + "fn g() ~:\n    stderr.write(\"normal\")\n"))
