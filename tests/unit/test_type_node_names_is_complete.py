"""`TYPE_NODE_NAMES` must hold every type node the grammar can produce, and one
predicate must be the only reader of it.

Seventeen readers asked "is this child a written type", and sixteen of them added
`name_t` to the set by hand -- in five different spellings. The seventeenth
(`foreach`) did not, so a `foreach` with a bare user type in the item position stopped
with an internal error while every other position took the same name (#595). The set is
now complete and `is_type_node` is the one question, so the two halves of that fault
each have a gate here: the set against the grammar, and the readers against the set.
"""
from __future__ import annotations

import re
from pathlib import Path

from lark import Token, Tree

from sushi_lang.semantics.ast_builder.utils.tree_navigation import is_type_node
from sushi_lang.semantics.typesys import TYPE_NODE_NAMES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAMMAR = PROJECT_ROOT / "sushi_lang" / "grammar.lark"
AST_BUILDER = PROJECT_ROOT / "sushi_lang" / "semantics" / "ast_builder"

# The two rules that spell a written type. Everything a `type` can be is an
# alternative of one of them, so their aliases ARE the set.
TYPE_RULES = ("?type", "?atom_type")


def _rule_body(text: str, rule: str) -> str:
    """The alternatives of one rule: its header line plus every continuation line."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"{rule}:"))
    body = [lines[start]]
    for ln in lines[start + 1:]:
        if not ln.startswith((" ", "\t")):
            break
        body.append(ln)
    return "\n".join(body)


def _grammar_type_aliases() -> set[str]:
    text = GRAMMAR.read_text()
    aliases: set[str] = set()
    for rule in TYPE_RULES:
        aliases |= set(re.findall(r"->\s*(\w+)", _rule_body(text, rule)))
    return aliases


def test_the_set_is_every_type_node_the_grammar_makes():
    missing = sorted(_grammar_type_aliases() - TYPE_NODE_NAMES)
    assert not missing, (
        f"the grammar makes type nodes the set does not hold: {missing}.\n"
        "A missing member is not a missing feature: the position that reads the set "
        "refuses a type every other position takes, and it refuses it as CE0002."
    )


def test_the_set_names_only_type_nodes():
    extra = sorted(TYPE_NODE_NAMES - _grammar_type_aliases())
    assert not extra, (
        f"the set holds names the grammar's type rules do not make: {extra}. "
        "A dead entry either renamed itself in the grammar or never existed."
    )


def test_one_reader_of_the_set():
    """`is_type_node` is the question. A second spelling is how a member goes missing."""
    readers = sorted(
        p.relative_to(PROJECT_ROOT).as_posix()
        for p in AST_BUILDER.rglob("*.py")
        if "TYPE_NODE_NAMES" in p.read_text()
    )
    assert readers == ["sushi_lang/semantics/ast_builder/utils/tree_navigation.py"], (
        f"the set is read in more than one place: {readers}. "
        "Ask `is_type_node(node)` instead -- it cannot forget a member."
    )


def test_a_bare_name_is_a_type_node():
    """The member that was missing, asserted directly."""
    assert is_type_node(Tree("name_t", [Token("NAME", "Point")]))
    assert is_type_node(Tree("qualified_name_t", [Token("NAME", "geo"), Token("NAME", "Vec")]))
    assert not is_type_node(Tree("block", []))
    assert not is_type_node(Token("NAME", "Point"))
