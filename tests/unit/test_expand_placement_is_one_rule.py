"""Where an `expand` may stand is ONE rule, in the collect pass (#1070).

The rule reads the written body: the iterable is the name of the function's value pack,
and the `expand` is not in a lambda body. `reject_misplaced_expands`
(`semantics/passes/collect/utils.py`) is its one emitter, and the analysis stops after
it. The typecheck pass and the unroll met a misplaced `expand` too, and each one emitted
CE0119 in its own words; both now raise the internal error CE0015. This gate refuses a
second emitter: the text `CE0119` occurs under `sushi_lang/` only in the module that
registers the code and in the module of the rule.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
ALLOWED = {
    Path("internals/errors/func.py"),
    Path("semantics/passes/collect/utils.py"),
}


def test_ce0119_has_one_emitter():
    hits = sorted(str(path.relative_to(ROOT)) for path in ROOT.rglob("*.py")
                  if "CE0119" in path.read_text(encoding="utf-8")
                  and path.relative_to(ROOT) not in ALLOWED)
    assert hits == [], (
        "CE0119 is named outside the one rule of the collect pass:\n  "
        + "\n  ".join(hits))


def test_the_rule_names_the_code():
    text = (ROOT / "semantics/passes/collect/utils.py").read_text(encoding="utf-8")
    assert "def reject_misplaced_expands(" in text
    assert "CE0119" in text
