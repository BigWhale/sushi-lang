"""The SLIR verifier over hand-built functions of a `Global` that owns (docs/design/ir.md 8.4, 8.13).

A `Global` is never a `Move` operand, and a `Drop` of a `Global` or of a part of one stands
only directly before an `Assign` into the same place: the rebind and the part write of
ir.md 8.10, rule 6. Each test gives the per-function check one function; it calls a Python
function over data and does not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.ids import LocalId
from sushi_lang.semantics.typesys import ArrayType, StructType

from tests.unit.test_slir_verifier import (
    BAD, BLANK, BOOL, I32, SPAN, STRING, _block, _dead, _flag, _function, _live, _local,
    _move, _passes, _refused, _ret, _set, _text,
)

GLOBAL_DROP = "a drop of a global before its store"
GLOBAL_MOVE = "no move out of a global"
OWNING_DROP = "drop of an owning type"

NAMED = StructType(name="Named", fields=(("name", STRING), ("n", I32)))
PAIR = ArrayType(base_type=STRING, size=2)
ZERO = p.Const(k.Int(0, I32))
TEXT = p.Global("unit$text", STRING)
OTHER = p.Global("unit$other", STRING)
LAST = p.Global("unit$last", NAMED)
NAMES = p.Global("unit$names", PAIR)
BLANK_OUT = _set(0, r.Use(p.Const(k.Blank())))


def _store(place: p.Place, span=SPAN) -> st.Assign:
    """The store of a rebind: the temporary `_1` moves into `place`."""
    return st.Assign(place, r.Use(_move(1)), span)


def _rebind(place: p.Place, *between: object, store: p.Place | None = None) -> list[object]:
    """`_1` holds the new value; the old value of `place` drops; then the store."""
    return [_live(1), _text(1), st.Drop(place, BAD), *between,
            _store(place if store is None else store), _dead(1)]


@pytest.mark.parametrize("place", [TEXT, p.Field(LAST, 0), p.Index(NAMES, ZERO)],
                         ids=["global", "field", "element"])
def test_a_drop_of_a_global_right_before_its_store_passes(place: p.Place):
    _passes(_function([BLANK, STRING], _block(0, _ret(), *_rebind(place), BLANK_OUT)))


def test_a_rebind_of_a_global_with_a_constant_passes():
    _passes(_function([BLANK], _block(
        0, _ret(), st.Drop(TEXT, SPAN), st.Assign(TEXT, r.Use(p.Const(k.Str(b"end"))), SPAN),
        BLANK_OUT)))


def test_a_drop_of_a_global_with_no_store_after_it_is_refused():
    _refused(_function([BLANK], _block(0, _ret(), st.Drop(TEXT, BAD))), GLOBAL_DROP)


def test_a_drop_of_a_global_with_a_statement_before_its_store_is_refused():
    _refused(_function([BLANK, STRING, I32],
                       _block(0, _ret(), *_rebind(TEXT, _set(2, r.Use(ZERO))))), GLOBAL_DROP)


@pytest.mark.parametrize("dropped,stored", [
    (TEXT, OTHER), (p.Field(LAST, 0), LAST), (p.Field(LAST, 0), p.Field(LAST, 1)),
    (p.Index(NAMES, ZERO), NAMES), (TEXT, _local(2)),
], ids=["another global", "the struct of the field", "a sibling field",
        "the array of the element", "a local"])
def test_a_drop_of_a_global_before_a_store_into_another_place_is_refused(
        dropped: p.Place, stored: p.Place):
    _refused(_function([BLANK, STRING, STRING],
                       _block(0, _ret(), *_rebind(dropped, store=stored))), GLOBAL_DROP)


def test_a_drop_if_set_of_a_global_is_refused():
    _refused(_function([BLANK, BOOL], _block(
        0, _ret(), _flag(1, True), st.DropIfSet(TEXT, LocalId(1), BAD),
        st.Assign(TEXT, r.Use(p.Const(k.Str(b"end"))), SPAN))), GLOBAL_DROP)


def test_a_drop_of_a_global_that_owns_nothing_is_refused():
    count = p.Global("unit$count", I32)
    _refused(_function([BLANK], _block(
        0, _ret(), st.Drop(count, BAD), st.Assign(count, r.Use(ZERO), SPAN))), OWNING_DROP)


def test_a_move_out_of_a_field_of_a_global_is_refused():
    _refused(_function([BLANK, STRING], _block(
        0, _ret(), _live(1), _set(1, r.Use(p.Move(p.Field(LAST, 0))), BAD), _dead(1))),
        GLOBAL_MOVE)
