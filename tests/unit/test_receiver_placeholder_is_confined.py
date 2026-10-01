"""The receiver placeholder is the compiler's, and one matcher reads it.

`ReceiverType` stands for the implementing type in the contract of a predefined perk
(`Eq`, `Ord`). A user perk still has no `Self`, so the placeholder is built in one place,
`register_predefined_perks`, and read by the implementation matcher and the display of
a type. A second constructor would let a contract nobody declared name its receiver.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
BUILDER = ROOT / "semantics" / "passes" / "collect" / "perks.py"
READERS = {
    ROOT / "semantics" / "typesys.py",
    ROOT / "semantics" / "passes" / "types" / "perks.py",
    BUILDER,
}


def _uses(path: Path) -> tuple[list[int], list[int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    built, named = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ReceiverType":
            built.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id == "ReceiverType":
            named.append(node.lineno)
    return built, named


def test_only_the_predefined_perks_build_the_placeholder():
    hits = [f"{p.relative_to(ROOT)}:{line}" for p in sorted(ROOT.rglob("*.py"))
            if p != BUILDER for line in _uses(p)[0]]
    assert hits == []


def test_only_the_matcher_reads_the_placeholder():
    hits = [f"{p.relative_to(ROOT)}:{line}" for p in sorted(ROOT.rglob("*.py"))
            if p not in READERS for line in _uses(p)[1]]
    assert hits == []


def test_the_control_finds_the_builder():
    assert _uses(BUILDER)[0]
