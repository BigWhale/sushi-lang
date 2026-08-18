"""The interposer's side table, driven directly (issue #371).

Every leak and double-free claim the suite makes rests on this table, and the one thing
it could not be tested for was a COLLISION: two tracked addresses landing on one slot.
A C program cannot ask the allocator for a colliding pair, which is why #371 was filed
rather than fixed.

It does not have to ask. `slot()` is a pure function of the key, so the fixture searches
for a pair and drives `tab_insert`/`tab_remove`/`tab_retire` with it. Deterministic, and
it needs no cooperation from the allocator.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = TESTS_DIR.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "leakcheck"
LEAKCHECK_DIR = PROJECT_ROOT / "tests" / "leakcheck"

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from run_tests import leakcheck_platform  # noqa: E402

pytestmark = pytest.mark.skipif(
    leakcheck_platform() is None,
    reason=f"the leak interposer is not supported on {sys.platform}",
)


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> Path:
    """The fixture, compiled against the real interposer source."""
    out = tmp_path_factory.mktemp("leakcheck_table") / "table_probe"
    cmd = ["cc", "-O0", "-I", str(LEAKCHECK_DIR), "-o", str(out),
           str(FIXTURES / "table_probe.c")]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        pytest.fail(f"could not compile the table probe:\n{result.stderr}")
    return out


@pytest.fixture(scope="module")
def results(probe) -> dict[str, str]:
    """Each `ok <name>` / `FAIL <name> ...` line the probe emitted, keyed by name."""
    proc = subprocess.run([str(probe)], capture_output=True, text=True, timeout=120)
    lines = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("ok", "FAIL"):
            lines[parts[1]] = line
    assert lines, f"the probe produced no results\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return lines


# Every property the table must hold. The first is #371; the rest are what a fix for it
# must not break, and each was a bug in its own right once.
PROPERTIES = [
    ("first_free_is_normal",
     "a tracked block's first free is RM_FREED"),
    ("double_free_survives_a_colliding_insert",
     "#371: an insert that collides must not consume another key's tombstone, or the "
     "genuine second free of that key reads as untracked and goes UNREPORTED"),
    ("first_free_of_c",
     "the same, for a key with no collision"),
    ("reissued_address_frees_normally",
     "#359: an address handed out again revives its own slot, so the next legitimate "
     "free is not reported as a double free"),
    ("retired_tombstone_stops_matching",
     "tab_retire drops the retained key, so an untracked allocation at a dead address "
     "cannot make the next free look like a double free"),
    ("own_slot_wins_over_an_earlier_foreign_one",
     "a key finds its OWN tombstone even when a foreign one sits earlier on the chain"),
    ("the_foreign_tombstone_was_left_alone",
     "and taking the own slot leaves the foreign tombstone intact"),
]


@pytest.mark.parametrize("name,why", PROPERTIES, ids=[p[0] for p in PROPERTIES])
def test_table_property(results, name, why):
    line = results.get(name)
    assert line is not None, f"the probe did not report `{name}`; it reported {sorted(results)}"
    assert line.startswith("ok "), f"{why}\n{line}"
