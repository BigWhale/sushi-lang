"""CE3015 is a question about ONE unit, and the typecheck pass answers it (#942).

The backend asked whether the module was linked into the PROGRAM, so an import in one
unit opened the gate for every other unit. The refusal now comes from the typecheck pass,
which reads the scope of the unit that holds the call, and the backend asks nothing.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]




def test_backend_emits_no_ce3015():
    hits = [str(path.relative_to(ROOT))
            for path in (ROOT / "sushi_lang" / "backend").rglob("*.py")
            if "CE3015" in path.read_text(encoding="utf-8")]
    assert hits == []








