"""The unary operators that keep the type of their operand are written in one place.

A declared type reaches a bare literal through every operator that keeps its type. `~` was
in no such set once, so `let u8 b = ~0` left its literal at the i32 default (#448). A second
copy of the set is a second rule, and it drifts. The gate reads the source: only
`semantics/operators.py` spells the set, and every other module imports it.
"""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "sushi_lang"
HOME = "semantics/operators.py"
THE_SET = frozenset({"neg", "~"})


def _spellings(source: str) -> list[int]:
    """The lines of each tuple, list or set literal whose members are the set."""
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Tuple, ast.List, ast.Set))
            and len(node.elts) == len(THE_SET)
            and all(isinstance(elt, ast.Constant) for elt in node.elts)
            and {elt.value for elt in node.elts} == THE_SET]


def test_control_the_scanner_sees_a_spelling():
    assert _spellings('x = ("~", "neg")\nif op in ["neg", "~"]:\n    pass\n') == [1, 2]
    assert _spellings('x = ("neg", "not", "~")\n') == []


def test_the_set_is_spelled_in_one_module():
    found = {path.relative_to(PACKAGE).as_posix(): lines
             for path in sorted(PACKAGE.rglob("*.py"))
             if (lines := _spellings(path.read_text(encoding="utf-8")))}
    assert list(found) == [HOME], (
        f"the type-keeping unary operators are spelled in {found}; import "
        f"TYPE_KEEPING_UNARY from {HOME}")
