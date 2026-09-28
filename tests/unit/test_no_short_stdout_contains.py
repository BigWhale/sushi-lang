"""Gate: a success fixture does not pass on almost any output (#1050).

A success fixture whose every stdout assertion is an `EXPECT_STDOUT_CONTAINS` of
SHORT_LIMIT characters or fewer passes on nearly every output: the order of the CONTAINS
lines is free, and a single digit is in most outputs. Such a fixture asserts
`EXPECT_STDOUT_EXACT` of the output its program computes.

The gate reads the fixture headers through `parse_test_metadata`, the reader the runner
uses. It reads text only and starts no compiler.
"""

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).parent.parent  # tests/

if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from test_metadata import (  # noqa: E402
    collect_fixtures,
    parse_test_metadata,
    should_run_runtime_test,
)

# The longest CONTAINS text that is still too short to assert an output.
SHORT_LIMIT = 3

# Fixtures that may keep a short CONTAINS, keyed by path relative to tests/. It is empty
# and stays empty: a fixture that cannot state its output has found a fault to file.
QUARANTINE: dict[str, str] = {}


def is_weak_stdout(fixture: Path) -> bool:
    """The fixture runs, and its every stdout assertion is a short CONTAINS."""
    if fixture.name.startswith("test_err_"):
        return False
    metadata = parse_test_metadata(fixture)
    if not should_run_runtime_test(fixture, metadata):
        return False
    if (metadata.expect_stdout_exact is not None
            or metadata.expect_stdout_exact_before_rebuild is not None):
        return False
    contains = metadata.expect_stdout_contains
    return bool(contains) and all(len(text) <= SHORT_LIMIT for text in contains)


def test_no_success_fixture_asserts_only_short_contains():
    fixtures = collect_fixtures(TESTS_ROOT)
    assert fixtures, "the collector found no fixture; the gate would pass on nothing"
    weak = [str(f.relative_to(TESTS_ROOT)) for f in fixtures
            if is_weak_stdout(f) and str(f.relative_to(TESTS_ROOT)) not in QUARANTINE]
    assert not weak, (
        f"{len(weak)} success fixture(s) assert stdout only with a CONTAINS of "
        f"{SHORT_LIMIT} characters or fewer; assert EXPECT_STDOUT_EXACT of the "
        f"measured output instead:\n  " + "\n  ".join(weak))


def test_quarantine_is_empty():
    assert QUARANTINE == {}


def _probe(tmp_path: Path, name: str, header: str) -> Path:
    path = tmp_path / name
    path.write_text(header + "\nfn main() i32:\n    println(1)\n    return Result.Ok(0)\n")
    return path


def test_gate_fires_on_a_single_short_contains(tmp_path):
    probe = _probe(tmp_path, "test_probe_short.sushi", '# EXPECT_STDOUT_CONTAINS: "1"\n')
    assert is_weak_stdout(probe)


def test_gate_fires_on_many_short_contains(tmp_path):
    probe = _probe(tmp_path, "test_probe_many.sushi",
                   '# EXPECT_STDOUT_CONTAINS: "1"\n# EXPECT_STDOUT_CONTAINS: "abc"\n')
    assert is_weak_stdout(probe)


def test_gate_passes_a_long_contains(tmp_path):
    probe = _probe(tmp_path, "test_probe_long.sushi",
                   '# EXPECT_STDOUT_CONTAINS: "1"\n# EXPECT_STDOUT_CONTAINS: "abcd"\n')
    assert not is_weak_stdout(probe)


def test_gate_passes_an_exact_stdout(tmp_path):
    probe = _probe(tmp_path, "test_probe_exact.sushi",
                   '# EXPECT_STDOUT_CONTAINS: "1"\n# EXPECT_STDOUT_EXACT: "1\\n"\n')
    assert not is_weak_stdout(probe)


def test_gate_ignores_an_error_fixture(tmp_path):
    probe = _probe(tmp_path, "test_err_probe.sushi",
                   '# EXPECT_ERROR_CODE: CE2002\n# EXPECT_STDOUT_CONTAINS: "1"\n')
    assert not is_weak_stdout(probe)
