"""One reader reads an import path, and the node's rule name says how (#638).

`use_stmt` holds one of three import nodes. Two of them spell a path as `NAME ("/" NAME)*`
and differ in the prefix they carry and the flag they set; the third holds a quoted
STRING and is its own reader. What each form must answer -- the path, the two flags, the
alias and the `public` marker, each with its span -- is pinned here, because the reader is
the only place those values are made.
"""
from __future__ import annotations

import re
from pathlib import Path


from sushi_lang.semantics.ast_builder.declarations.imports import (
    BRACKETED_IMPORTS, IMPORT_RULES)

GRAMMAR = Path(__file__).resolve().parents[2] / "sushi_lang" / "grammar.lark"




# -- the table covers the grammar ------------------------------------------------

def test_the_table_and_the_quoted_form_cover_every_import_the_grammar_spells():
    """An import form the grammar adds gets a row, or a reader of its own."""
    text = GRAMMAR.read_text()
    line = re.search(r"^use_stmt: (.+)$", text, re.MULTILINE)
    assert line is not None
    written = set(re.findall(r"\w+_import", line.group(1)))
    assert written == set(IMPORT_RULES)
    assert written == set(BRACKETED_IMPORTS) | {"user_import"}


def test_a_bracketed_form_spells_a_use_path_and_the_quoted_form_does_not():
    """What puts a form in the table: the grammar gives it the shared path rule."""
    text = GRAMMAR.read_text()
    for rule in IMPORT_RULES:
        body = re.search(rf"^{rule}: (.+)$", text, re.MULTILINE)
        assert body is not None, f"no rule named {rule}"
        assert ("use_path" in body.group(1)) is (rule in BRACKETED_IMPORTS)


# -- the path, and which flag the form sets -------------------------------------













# -- `public use`, on each of the three forms -----------------------------------





# -- `use ... as NAME`, on each of the three forms ------------------------------





# -- the span of the statement itself -------------------------------------------



# -- a path node with no path is a malformed tree --------------------------------



