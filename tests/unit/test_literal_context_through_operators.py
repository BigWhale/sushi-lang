"""A declared type reaches a bare literal through every operator that KEEPS its type.

One recursion carries a declared numeric type down to the literal leaves, and the
operators it descends through are closed sets. `~` was in none of them: it is neither a
`BinaryOp` nor the negated bare literal the leaf stamper unwraps, so `let u8 b = ~0` left
its literal at the i32 default and the declaration was CE2002 (#448). Unary minus was only
half in -- `-(1 + 2)` failed the same way.

The table below asks the question per (operator, width) rather than per symptom, so a set
that loses a member again cannot pass. `not` is the control: it yields a bool, it is NOT
type-preserving, and a rule that descends through every unary operator fails that row.
"""
from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# One expression per operator, built only from bare literals -- the declared type is the
# only thing that can type them -- and valued so it is legal at every width below.

# Both signednesses, narrowest and widest of each.

# Every way the pass reports a literal that never took its context type.




















def test_the_type_preserving_set_lives_in_one_place():
    """One list of operators. A second copy is a second rule, and it will drift."""
    sites = [path.relative_to(PROJECT_ROOT)
             for path in sorted((PROJECT_ROOT / "sushi_lang").rglob("*.py"))
             if "_TYPE_PRESERVING_UNARY" in path.read_text(encoding="utf-8")]
    assert len(sites) == 1, sites
    assert sites[0].as_posix() == "sushi_lang/semantics/passes/types/propagation.py", sites
