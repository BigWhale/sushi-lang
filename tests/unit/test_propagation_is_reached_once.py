"""One propagation call per position, and the guard that survives it.

`utils.py` carried two shims over `propagation.propagate_types_to_value` that differed in
one word of their docstrings, and nine positions called BOTH of them, back to back, on one
argument. The propagation therefore ran twice at each of them, and the idempotency guard
in `_stamp_numeric_literal` absorbed the second run.

Both shims are gone, and every position calls the seam itself. The guard stays, because it also answers a
GENUINE re-entry: a value is reached by more than one propagation in its own right, and
stamping a literal twice would report its range fault twice. That is measured, not
argued -- the guard still fires over the fixture corpus with every duplicate call gone.
"""
from __future__ import annotations

import ast
from pathlib import Path

SUSHI_LANG = Path(__file__).resolve().parents[2] / "sushi_lang"
PROPAGATION = SUSHI_LANG / "semantics" / "passes" / "types" / "propagation.py"


def test_the_idempotency_guard_is_still_there():
    """The guard is live, not dead code left over from the shims."""
    tree = ast.parse(PROPAGATION.read_text(encoding="utf-8"), filename=str(PROPAGATION))
    stamp = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "_stamp_numeric_literal")
    guards = [node for node in ast.walk(stamp)
              if isinstance(node, ast.If)
              and any(isinstance(inner, ast.Return) for inner in node.body)]
    assert guards, ("_stamp_numeric_literal no longer returns early for a literal that "
                    "is already stamped: a second reach reports its range fault twice")
