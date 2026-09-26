"""CE3015 is a question about ONE unit, and the typecheck pass answers it (#942).

The backend asked whether the module was linked into the PROGRAM, so an import in one
unit opened the gate for every other unit. The refusal now comes from the typecheck pass,
which reads the scope of the unit that holds the call, and the backend asks nothing.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _codes(reporter) -> list[str]:
    return [d.code for d in reporter.items if d.code.startswith("CE")]


def test_backend_emits_no_ce3015():
    hits = [str(path.relative_to(ROOT))
            for path in (ROOT / "sushi_lang" / "backend").rglob("*.py")
            if "CE3015" in path.read_text(encoding="utf-8")]
    assert hits == []


def test_one_call_without_the_import_is_one_ce3015(analyze):
    reporter = analyze(
        'fn main() i32:\n'
        '    let string s = "Mostly Harmless"\n'
        '    println(s.upper())\n'
        '    return Result.Ok(0)\n')
    assert _codes(reporter) == ["CE3015"]


def test_the_import_of_the_unit_opens_the_gate(analyze):
    reporter = analyze(
        'use <collections/strings>\n'
        'fn main() i32:\n'
        '    let string s = "Mostly Harmless"\n'
        '    println(s.upper())\n'
        '    return Result.Ok(0)\n')
    assert _codes(reporter) == []


def test_an_aliased_import_opens_the_gate(analyze):
    reporter = analyze(
        'use <collections/strings> as cs\n'
        'fn main() i32:\n'
        '    let string s = "Mostly Harmless"\n'
        '    println(s.upper())\n'
        '    return Result.Ok(0)\n')
    assert _codes(reporter) == []


def test_clone_and_is_empty_need_no_import(analyze):
    reporter = analyze(
        'fn main() i32:\n'
        '    let string s = "Mostly Harmless"\n'
        '    let string t = s.clone()\n'
        '    println(t.is_empty())\n'
        '    return Result.Ok(0)\n')
    assert _codes(reporter) == []
