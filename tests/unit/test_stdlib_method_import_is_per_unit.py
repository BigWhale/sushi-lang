"""The backend never decides whether a unit may call a stdlib method.

CE3015, the import gate for a stdlib method, is retired (epic #1251): a stdlib method on
a built-in type needs no import (R1 of docs/design/extension-visibility.md), and how the
compiler emits a method never decides whether a user may call it (R10). The backend once
asked whether the module was linked into the PROGRAM (#942). No backend module may name
the code again.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_backend_emits_no_ce3015():
    hits = [str(path.relative_to(ROOT))
            for path in (ROOT / "sushi_lang" / "backend").rglob("*.py")
            if "CE3015" in path.read_text(encoding="utf-8")]
    assert hits == []
