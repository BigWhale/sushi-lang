"""The symbol of a body in its module, which SLIR lowering gives a function (docs/design/ir.md 8.2).

A method of a perk implementation has the perk symbol of its target. The perk
implementation table holds the method, and a body with a receiver that the table does not
hold is an internal error, never a free function. The body states the receiver
(`ShirBody.has_receiver`, set from the declaration); the name of a local does not. Each test calls a Python function over
data and does not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import Block, FuncDef
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.passes.collect.perks import PerkImplementationTable
from sushi_lang.semantics.shir.body import ExpandCopies, Instance, ShirBody, ShirLocal, ShirParam
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.slir.lower_signature import symbol_of
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.semantics.unit_symbols import function_symbol

LOC = Span(1, 1, 1, 9)


def _body(receiver: bool, name: str = "x") -> ShirBody:
    params = [ShirParam(LocalId(0), BuiltinType.I32, Convention.GUARANTEED, LOC, False)]
    locals_ = [ShirLocal(LocalId(0), name, None, None)]
    return ShirBody(DeclNames().declare("show"), Instance(), params, locals_,
                    ShirBlock(ShirId(0), LOC, []), ExpandCopies(), has_receiver=receiver)


def _decl() -> FuncDef:
    return FuncDef(LOC, "show", [], BuiltinType.I32, Block(LOC, []))


def test_a_free_function_has_the_symbol_of_its_unit():
    assert symbol_of(_decl(), "u", _body(receiver=False), PerkImplementationTable()) \
        == function_symbol("show", "u")


def test_a_method_that_no_perk_implementation_holds_is_an_internal_error():
    with pytest.raises(InternalCompilerError) as caught:
        symbol_of(_decl(), "u", _body(receiver=True), PerkImplementationTable())
    assert caught.value.code == "CE0000"


def test_a_parameter_named_self_is_no_receiver():
    assert symbol_of(_decl(), "u", _body(receiver=False, name="self"),
                     PerkImplementationTable()) == function_symbol("show", "u")
