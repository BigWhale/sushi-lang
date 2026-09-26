"""One table sorts a unit's top-level declarations, and it covers the grammar.

`program` holds a `_NEWLINE` (filtered away), a `DOC_BLOCK` (a Token) or a `toplevel`,
and `toplevel` has no `?`, so Lark always builds the node. Every Tree child of
`program` is therefore a `toplevel` that holds exactly ONE declaration, and one table
keyed on the rule name answers for every kind. A kind the grammar adds must get a
table row, or this gate fails.
"""
from __future__ import annotations

import re
from pathlib import Path

from sushi_lang.semantics.ast_builder.declarations.toplevel import (
    DECLARATION_TABLE, PRE_PASS_RULES, TOPLEVEL_RULES)

GRAMMAR = Path(__file__).resolve().parents[2] / "sushi_lang" / "grammar.lark"



def _alternatives(rule: str) -> set[str]:
    """The alternatives of one rule of `grammar.lark`, as bare names."""
    text = GRAMMAR.read_text()
    line = re.search(rf"^{rule}: (.+)$", text, re.MULTILINE)
    assert line is not None, f"no rule named {rule}"
    return {part.strip() for part in line.group(1).split("|")}




def test_the_table_covers_every_alternative_of_toplevel():
    """A declaration kind the grammar spells must have a row, or a case of its own."""
    assert _alternatives("toplevel") == set(TOPLEVEL_RULES)
    assert set(TOPLEVEL_RULES) == set(DECLARATION_TABLE) | set(PRE_PASS_RULES) | {"extend_stmt"}


def test_program_holds_nothing_but_a_toplevel_a_doc_block_and_a_newline():
    """The invariant that makes a second, bare-node dispatch unreachable."""
    assert _alternatives("program") == {"(_NEWLINE", "DOC_BLOCK", "toplevel)+"}








