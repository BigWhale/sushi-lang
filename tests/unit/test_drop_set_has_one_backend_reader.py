"""The `Drop` set has one reader per layer (#791 row 1).

`owns_resource` takes the set with no default, because a wrong set silently copies a
handle. The back end and the compiler driver hold a `codegen`, so they read the set
through `backend/ownership.py:drops_of`. The semantic passes may not import the back
end, so they read it through `semantics/drop_set.py:drop_type_names`. No other module
spells the perk-table lookup.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SPELLING = 'by_perk.get("Drop"'
BACKEND_SEAM = ROOT / "backend" / "ownership.py"
SEMANTIC_SEAM = ROOT / "semantics" / "drop_set.py"


def _readers() -> list[str]:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        for line in path.read_text().splitlines():
            if SPELLING in line:
                found.append(str(path.relative_to(ROOT)))
    return found


def test_the_seams_are_measured():
    """The control: each seam spells the lookup, so the scan can see it."""
    assert SPELLING in BACKEND_SEAM.read_text()
    assert SPELLING in SEMANTIC_SEAM.read_text()


def test_one_reader_per_layer():
    assert _readers() == ["backend/ownership.py", "semantics/drop_set.py"], _readers()
