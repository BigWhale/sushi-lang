"""The record of the constant that a `ConstRef` names: one lookup for both passes.

`NamespaceTable.constant_record` answers behind an alias, then the constant or `var` of the
scope. The `borrow` pass reads it as `typecheck` does: a lookup that misses is an internal
error, never a false user CE2400. The tests call the lookup over
tables built by hand; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.namespaces import NamespaceTable
from sushi_lang.semantics.passes.borrow import BorrowChecker
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import ShirId

SPAN = Span(2, 5, 2, 7)


class _NoConstants:
    def lookup(self, _name, scope=None):
        return None


def test_a_constant_that_no_table_holds_has_no_record() -> None:
    assert NamespaceTable().constant_record("NO_SUCH_CONSTANT", _NoConstants()) is None


def test_the_borrow_pass_refuses_a_lookup_that_misses() -> None:
    names = DeclNames()
    ref = e.ConstRef(ShirId(1), SPAN, names.refer("NO_SUCH_CONSTANT"))
    checker = SimpleNamespace(names=NamespaceTable(), tables=SimpleNamespace(constants=_NoConstants()),
                              decl_name=names.name_of)
    with pytest.raises(InternalCompilerError):
        BorrowChecker.constant_record(checker, ref)  # type: ignore[arg-type]
