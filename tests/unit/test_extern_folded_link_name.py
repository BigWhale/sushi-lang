"""The folded link name of an extern declaration (#1089), which the extern table holds.

`fold_link_names` folds a link name that a declaration writes as a string constant, and
each check of the declaration reads it from the table. The key of a row is the namespace,
the name and the span of the name, so a copy of the declaration node finds its row too.
Each test calls a Python function over data and does not run the compiler.
"""
from __future__ import annotations

from dataclasses import replace

from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import ExternalDecl
from sushi_lang.semantics.passes.collect.externals import ExternalTable
from sushi_lang.semantics.typesys import BuiltinType

NAME = Span(3, 8, 3, 13)


def _decl() -> ExternalDecl:
    return ExternalDecl(NAME, "probe", [], BuiltinType.I32, "C_NAME", name_span=NAME)


def test_a_copy_of_a_declaration_reads_its_folded_link_name():
    table, decl = ExternalTable(), _decl()
    table.fold("c", decl, "real_probe")
    assert table.link_name_of("c", replace(decl)) == "real_probe"


def test_a_declaration_with_no_folded_row_reads_its_written_link_name():
    table, decl = ExternalTable(), _decl()
    table.fold("other", decl, "real_probe")
    assert table.link_name_of("c", decl) == "C_NAME"
