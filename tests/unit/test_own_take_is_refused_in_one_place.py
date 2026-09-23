"""CE2434 has ONE home: the AST builder, for the direct and the nested form (#791 row 4).

The builder refused `Own(nom x)` while it built the pattern, and the borrow pass refused
`Own(Inner.Word(nom s))` with a second emitter and a different help line. One rule, two
homes. The builder walks the whole pattern tree, so it refuses both forms before any
pass runs, and the borrow pass has no emitter for the code.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

_TYPES = (
    "enum Inner:\n    Word(string)\n    Pair(string, string)\n    Nil\n\n"
    "enum Outer:\n    Full(Own@(Inner))\n    Deep(Own@(Outer))\n    Empty\n\n"
)


def _program(pattern: str) -> str:
    return (_TYPES
            + "fn main() i32:\n"
            + "    let Outer r = Outer.Empty\n"
            + "    match nom r:\n"
            + f"        {pattern} -> println(\"hit\")\n"
            + "        Outer.Full(_) -> println(\"other\")\n"
            + "        Outer.Deep(_) -> println(\"deep\")\n"
            + "        Outer.Empty -> println(\"empty\")\n"
            + "    return Result.Ok(0)\n")


def _builder_diagnostics(source: str) -> list:
    reporter = Reporter(source=source, filename="main.sushi")
    parse_to_ast(source, reporter=reporter)
    return reporter.items


def test_one_emit_site():
    sites = []
    for path in sorted(ROOT.rglob("*.py")):
        if "internals/errors" in str(path):
            continue
        if "CE2434" in path.read_text():
            sites.append(str(path.relative_to(ROOT)))
    assert sites == ["semantics/ast_builder/statements/matching.py"], sites


@pytest.mark.parametrize(("pattern", "count"), [
    ("Outer.Full(Own(nom s))", 1),
    ("Outer.Full(Own(Inner.Word(nom s)))", 1),
    ("Outer.Full(Own(Inner.Pair(nom a, nom b)))", 2),
    ("Outer.Deep(Own(Outer.Full(Own(Inner.Word(nom s)))))", 1),
])
def test_the_builder_refuses_every_form(pattern, count):
    assert [d.code for d in _builder_diagnostics(_program(pattern))] == ["CE2434"] * count


@pytest.mark.parametrize("pattern", [
    "Outer.Full(Own(nom s))",
    "Outer.Full(Own(Inner.Word(nom s)))",
])
def test_both_forms_carry_one_help_line(pattern):
    (diagnostic,) = _builder_diagnostics(_program(pattern))
    helps = [str(getattr(sub, "message", sub)) for sub in diagnostic.sub]
    assert len(helps) == 1 and "bind the pointee by value" in helps[0], helps


@pytest.mark.parametrize("pattern", [
    "Outer.Full(Own(s))",
    "Outer.Full(Own(Inner.Word(s)))",
    "Outer.Full(Own(poke s))",
])
def test_a_binding_that_takes_nothing_is_not_refused(pattern):
    assert _builder_diagnostics(_program(pattern)) == []
