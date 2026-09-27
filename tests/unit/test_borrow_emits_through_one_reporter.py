"""Every borrow-pass diagnostic goes through `checker.err` (#1032).

The pass checks a loop body in two rounds, and the first is a dry run whose diagnostics
`checker.err` suppresses. A direct emit on the raw reporter skips that suppression and
tells one fault twice, so the gate refuses every path to the raw reporter: the `er`
module's emit functions, an emit method called on a `reporter` attribute, and a
`reporter` attribute handed to a call.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
BORROW_PASS = ROOT / "semantics" / "passes" / "borrow"
RAW_EMITS = frozenset({"emit", "emit_with", "emit_exception"})


def raw_reporter_emits(tree: ast.AST) -> list[int]:
    """The line of every diagnostic that does not go through the pass's reporter."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in RAW_EMITS:
            owner = func.value
            if isinstance(owner, ast.Name) and owner.id == "er":
                lines.append(node.lineno)
                continue
            if isinstance(owner, ast.Attribute) and owner.attr == "reporter":
                lines.append(node.lineno)
                continue
        passed = [*node.args, *(kw.value for kw in node.keywords)]
        if any(isinstance(arg, ast.Attribute) and arg.attr == "reporter"
               for arg in passed):
            lines.append(node.lineno)
    return sorted(set(lines))


def test_the_gate_fires_on_each_raw_path():
    control = ast.parse(
        "er.emit(checker.reporter, er.ERR.CE2430, span)\n"
        "er.emit_with(r, er.ERR.CE2430, span)\n"
        "self.reporter.emit(diag)\n"
        "report_it(checker.reporter, span)\n"
    )
    assert raw_reporter_emits(control) == [1, 2, 3, 4]


def test_the_gate_lets_the_pass_reporter_through():
    tree = ast.parse(
        "checker.err.emit(er.ERR.CE2430, span, name=n)\n"
        "checker.err.emit_with(er.ERR.CE2412, span).emit()\n"
        "self.reporter.enter_body(func)\n"
    )
    assert raw_reporter_emits(tree) == []


def test_the_borrow_pass_has_no_raw_reporter_emit():
    found = {
        f"{path.relative_to(ROOT)}:{line}"
        for path in sorted(BORROW_PASS.rglob("*.py"))
        for line in raw_reporter_emits(ast.parse(path.read_text(encoding="utf-8")))
    }
    assert not found, (
        "a borrow diagnostic must go through `checker.err`, which suppresses the "
        f"dry-run round of a loop body: {sorted(found)}")
