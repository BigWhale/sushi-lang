"""One answer to the question "which compiler does a subprocess test run?" (#530).

PATH `sushic` is the editable install of whichever checkout owns the active
virtual environment. In a git worktree, and in any second clone, that is a
DIFFERENT tree than the one the in-process layer imports, and the split is
silent: the suite stays green while the new tree's subprocess coverage proves
nothing. Resolve the driver the way `tests/run_tests.py` already does -- from
the repository that holds this file -- and both layers test one tree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: argv[0] for every subprocess that spawns the compiler.
SUSHIC = str(PROJECT_ROOT / "sushic")

#: The driver is a tracked file of the checkout, so it is there unless the
#: tree is incomplete.
SUSHIC_AVAILABLE = (PROJECT_ROOT / "sushic").is_file()

#: The one skip guard for a subprocess-backed test.
needs_sushic = pytest.mark.skipif(
    not SUSHIC_AVAILABLE, reason=f"no compiler driver at {SUSHIC}")
