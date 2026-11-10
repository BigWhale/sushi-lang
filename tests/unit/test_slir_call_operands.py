"""The operands of a call, which a `CallValue` of SLIR lowering and a `Call` statement share
(docs/design/ir.md 8.9, 8.10 rule 3).

The store of a call marks each `Move` operand of it moved: each argument, and the indirect
callee too. The statement reads the same operands in the same order. Each test calls a
Python function over data and does not run the compiler.
"""
from __future__ import annotations

from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.ids import LocalId
from sushi_lang.semantics.slir.lower_values import CallValue, moves_of

SPAN = Span(1, 1, 1, 9)
CLOSURE, ARGUMENT, DEST = p.Local(LocalId(1)), p.Local(LocalId(2)), p.Local(LocalId(3))


def test_the_store_of_an_indirect_call_moves_its_callee_and_its_arguments():
    value = CallValue(st.Indirect(p.Move(CLOSURE)), ((p.Move(ARGUMENT), Convention.OWNED),))
    assert moves_of(value) == [ARGUMENT, CLOSURE]


def test_a_call_value_and_its_statement_read_the_same_operands():
    callee = st.Indirect(p.Move(CLOSURE))
    args = ((p.Move(ARGUMENT), Convention.OWNED),)
    statement = st.Call(DEST, callee, args, SPAN)
    assert moves_of(CallValue(callee, args)) == [
        part.place for part in st.operands_of(statement) if isinstance(part, p.Move)]
