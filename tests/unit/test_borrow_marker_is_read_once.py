"""The `peek` / `poke` marker string is read in ONE module: `semantics/param_modes.py` (#779)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.semantics.param_modes import ParamMode, borrow_mode, receiver_mode
from sushi_lang.semantics.typesys import BorrowMode

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SEAM = ROOT / "semantics" / "param_modes.py"
MARKERS = frozenset({"peek", "poke"})


def marker_compares(tree: ast.AST) -> list[int]:
    """The line of every `==` / `!=` that tests a value against a marker literal."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        tests_equality = any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops)
        names_a_marker = any(isinstance(o, ast.Constant) and o.value in MARKERS
                             for o in operands)
        if tests_equality and names_a_marker:
            lines.append(node.lineno)
    return sorted(lines)


def test_the_gate_fires_on_a_marker_compare():
    control = ast.parse('a = BorrowMode.POKE if m == "poke" else BorrowMode.PEEK\n'
                        'b = x != "peek"\n'
                        'c = "peek" == y\n')
    assert marker_compares(control) == [1, 2, 3]


def test_the_gate_ignores_a_token_type_compare():
    assert marker_compares(ast.parse('ok = tok.type == "BORROW_MODE"\n')) == []


def test_no_marker_compare_outside_the_seam():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        if path == SEAM:
            continue
        for line in marker_compares(ast.parse(path.read_text(), str(path))):
            offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert offenders == [], (
        "read a marker through param_modes.borrow_mode / receiver_mode: "
        + ", ".join(offenders))


@pytest.mark.parametrize("marker,mode", [("peek", BorrowMode.PEEK),
                                         ("poke", BorrowMode.POKE)])
def test_borrow_mode_reads_each_marker(marker, mode):
    assert borrow_mode(marker) is mode


@pytest.mark.parametrize("bad", ["nom", "Poke", "", "borrow"])
def test_borrow_mode_refuses_a_bad_marker(bad):
    with pytest.raises(ValueError):
        borrow_mode(bad)


@pytest.mark.parametrize("bad", ["Poke", "", "borrow", "self"])
def test_receiver_mode_refuses_a_bad_marker(bad):
    with pytest.raises(ValueError):
        receiver_mode(bad)


def test_the_by_pointer_modes_name_their_borrow_mode():
    assert ParamMode.PEEK.borrow_mode is BorrowMode.PEEK
    assert ParamMode.POKE.borrow_mode is BorrowMode.POKE


@pytest.mark.parametrize("mode", [ParamMode.BORROW, ParamMode.NOM])
def test_a_by_value_mode_has_no_borrow_mode(mode):
    with pytest.raises(ValueError):
        _ = mode.borrow_mode
