"""Outside the semantic passes, `drops_of` is the one reader of the `Drop` set (#791 row 1).

`owns_resource` takes the set with no default, because a wrong set silently copies a
handle. The back end and the compiler driver hold a `codegen`, so they read the set
through `backend/ownership.py:drops_of` and never spell the perk-table lookup again.
The semantic passes may not import the back end, so they keep their own reader.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SPELLING = 'by_perk.get("Drop"'
SEAM = ROOT / "backend" / "ownership.py"


def _readers() -> list[str]:
    found = []
    for area in ("backend", "compiler"):
        for path in sorted((ROOT / area).rglob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if SPELLING in line:
                    found.append(f"{path.relative_to(ROOT)}:{number}")
    return found


def test_the_seam_is_measured():
    """The control: the seam itself spells the lookup, so the scan can see it."""
    assert SPELLING in SEAM.read_text()


def test_drops_of_is_the_one_reader():
    readers = _readers()
    assert readers == ["backend/ownership.py:" + str(
        next(n for n, line in enumerate(SEAM.read_text().splitlines(), 1)
             if SPELLING in line))], readers
