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

import pytest
from lark import Tree

from sushi_lang.internals.diagnostics import AstBuilderICE
from sushi_lang.internals.parser import build_parser
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import UseStatement
from sushi_lang.semantics.ast_builder import ASTBuilder
from sushi_lang.semantics.ast_builder.declarations.imports import (
    BRACKETED_IMPORTS, IMPORT_RULES, parse_usestatement)

GRAMMAR = Path(__file__).resolve().parents[2] / "sushi_lang" / "grammar.lark"


def _use(line: str) -> UseStatement:
    """The one `UseStatement` of a unit whose only declaration is `line`."""
    program = ASTBuilder().build(build_parser().parse(line + "\n"))
    assert len(program.uses) == 1
    return program.uses[0]


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

def test_a_stdlib_import_of_one_segment():
    use = _use("use <math>")
    assert use.path == "math"
    assert (use.is_stdlib, use.is_library) == (True, False)


def test_a_stdlib_import_joins_its_segments_with_a_slash():
    use = _use("use <io/fs>")
    assert use.path == "io/fs"
    assert (use.is_stdlib, use.is_library) == (True, False)


def test_a_stdlib_import_of_three_segments():
    """More than two segments joins the same way: nothing counts them."""
    assert _use("use <a/b/c>").path == "a/b/c"


def test_a_library_import_carries_the_lib_prefix():
    use = _use("use <lib/shapes>")
    assert use.path == "lib/shapes"
    assert (use.is_stdlib, use.is_library) == (False, True)


def test_a_library_import_of_more_than_one_segment():
    use = _use("use <lib/shapes/plane>")
    assert use.path == "lib/shapes/plane"
    assert (use.is_stdlib, use.is_library) == (False, True)


def test_a_user_import_loses_its_quotes_and_sets_no_flag():
    use = _use('use "helpers/geometry"')
    assert use.path == "helpers/geometry"
    assert (use.is_stdlib, use.is_library) == (False, False)


# -- `public use`, on each of the three forms -----------------------------------

@pytest.mark.parametrize("line,path,stdlib,library", [
    ("public use <math>", "math", True, False),
    ("public use <io/fs>", "io/fs", True, False),
    ("public use <lib/shapes>", "lib/shapes", False, True),
    ('public use "helpers/geometry"', "helpers/geometry", False, False),
])
def test_public_changes_nothing_but_the_marker(line, path, stdlib, library):
    use = _use(line)
    assert (use.path, use.is_stdlib, use.is_library) == (path, stdlib, library)
    assert use.is_public is True
    assert use.public_span == Span(line=1, col=1, end_line=1, end_col=7)


def test_a_plain_use_has_no_public_span():
    use = _use("use <math>")
    assert use.is_public is False
    assert use.public_span is None


# -- `use ... as NAME`, on each of the three forms ------------------------------

@pytest.mark.parametrize("line,path,stdlib,library,col", [
    ("use <math> as m", "math", True, False, 15),
    ("use <io/fs> as fs", "io/fs", True, False, 16),
    ("use <lib/shapes> as sh", "lib/shapes", False, True, 21),
    ('use "helpers/geometry" as geo', "helpers/geometry", False, False, 27),
])
def test_an_alias_changes_nothing_but_the_alias(line, path, stdlib, library, col):
    use = _use(line)
    assert (use.path, use.is_stdlib, use.is_library) == (path, stdlib, library)
    assert use.alias is not None
    assert use.alias_span == Span(line=1, col=col, end_line=1, end_col=col + len(use.alias))


def test_a_plain_use_has_no_alias():
    use = _use("use <math>")
    assert use.alias is None
    assert use.alias_span is None


# -- the span of the statement itself -------------------------------------------

@pytest.mark.parametrize("line", [
    "use <math>", "use <lib/shapes>", 'use "helpers/geometry"',
    "public use <math>", "use <math> as m",
])
def test_the_statement_spans_the_whole_use(line):
    """Every form spans the statement, and the trailing newline closes it."""
    assert _use(line).loc == Span(line=1, col=1, end_line=2, end_col=1)


# -- a path node with no path is a malformed tree --------------------------------

def _use_stmt_without_its_path(line: str) -> Tree:
    """The `use_stmt` of `line`, with the `use_path` taken out of the import node."""
    tree = build_parser().parse(line + "\n")
    stmt = next(n for n in tree.iter_subtrees() if n.data == "use_stmt")
    node = next(c for c in stmt.children
                if isinstance(c, Tree) and c.data in ("stdlib_import", "lib_import"))
    node.children = [c for c in node.children
                     if not (isinstance(c, Tree) and c.data == "use_path")]
    return stmt


@pytest.mark.parametrize("line,kind", [
    ("use <math>", "stdlib_import"),
    ("use <lib/shapes>", "lib_import"),
])
def test_an_import_node_with_no_path_is_an_ice_that_names_it(line, kind):
    with pytest.raises(AstBuilderICE) as caught:
        parse_usestatement(_use_stmt_without_its_path(line), ASTBuilder())
    assert caught.value.code == "CE0002"
    assert caught.value.params["node"] == kind
    assert caught.value.params["detail"] == "missing use_path"
    opening = line.index("<") + 1
    assert caught.value.span == Span(
        line=1, col=opening, end_line=1, end_col=len(line) + 1)
