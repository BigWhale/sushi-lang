"""One reader takes a bound in a type position: the top level of an `extend` target (#1070).

The grammar accepts `T: Clone` in every type-argument list and `(T: Clone)` as a
parenthesized type, because the parser cannot know the position. The AST builder must
then read the two nodes in ONE place, the target reader, and refuse them everywhere
else with CE6110. A second reader of the node names is a position that takes a bound
which no rule checks.

The gate scans the Python source: the two node names are spelled only in the target
reader, and in the set of type-node names that `is_type_node` reads.
"""
from __future__ import annotations

from pathlib import Path

SUSHI_LANG = Path(__file__).resolve().parents[2] / "sushi_lang"
NODE_NAMES = ("bounded_type_arg", "bounded_paren_t")
READERS = {
    "semantics/ast_builder/types/generics.py",
    "semantics/typesys.py",
}


def _spellers() -> set[str]:
    found = set()
    for path in SUSHI_LANG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(f'"{name}"' in text or f"'{name}'" in text for name in NODE_NAMES):
            found.add(path.relative_to(SUSHI_LANG).as_posix())
    return found


def test_the_bound_nodes_have_one_reader():
    assert _spellers() == READERS


def test_the_type_parser_refuses_through_the_reader():
    """The CE6110 arm of the type parser names the reader's set, not the node names."""
    text = (SUSHI_LANG / "semantics/ast_builder/types/parser.py").read_text(encoding="utf-8")
    assert "generics.BOUNDED_ARGUMENT_NODES" in text
    assert "generics.reject_misplaced_bound" in text
