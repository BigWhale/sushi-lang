"""Sanity checks for the unit-test infrastructure itself."""
from __future__ import annotations

from pathlib import Path

from sushic_path import SUSHIC, SUSHIC_AVAILABLE










# Every skip site across this layer is guarded on `needs_sushic`, which reads the
# driver of THIS checkout. A guard that answers False turns the whole subprocess
# layer into silent skips while the suite still reports green, so the same
# condition is asserted here.
def test_the_compiler_under_test_is_this_checkout():
    """The subprocess layer runs the tree it sits in, not whichever is on PATH (#530)."""
    assert SUSHIC_AVAILABLE, f"no compiler driver at {SUSHIC}"
    assert Path(SUSHIC).parent == Path(__file__).resolve().parents[2]
