"""SHIR to SLIR: the direct walks (docs/design/ir.md 7.7, 8.6, 8.9).

A `Cursor` is `Len` into a local, then the shape of a counted loop: the binder reads the
element at the counter. A `HashMapCursor` has a hidden cursor local, and each round a `Call`
of the `cursor_next` row of its view writes a view local of a `Maybe`; a switch on its tag
leaves the loop or goes to the body, and the binder reads the payload. SLIR does not see the
buckets. A `continue` goes to the header.

A bare binder of an owning item is a view local, and a `peek` or a `poke` binder is an address
local (`lower_control._bind`), in a region over the root of the container. The container
is the place of its receiver, or a new value in a temporary of the loop statement, which
drops at the end of the statement. The loop statement holds the cursor and the counter too.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
from sushi_lang.semantics.method_effects import CURSOR_NEXT
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Builtin
from sushi_lang.semantics.shir.loop_kind import Cursor, HashMapCursor
from sushi_lang.semantics.slir import lower_arrays as arrays
from sushi_lang.semantics.slir import lower_control as ctl
from sushi_lang.semantics.slir import lower_views as views
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.typesys import (
    BuiltinType, EnumType, IteratorType, PointerType, deref_type,
)

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def loop(low: Lowering, stmt: s.Loop) -> None:
    """A loop, by the kind that typecheck gave it: a direct walk here, any other in
    `lower_control.py`."""
    source = stmt.source
    kind = low.typeck.loop_kind_of(stmt.shir_id)
    if isinstance(source, s.Items) and isinstance(kind, Cursor):
        _cursor(low, stmt, source)
    elif isinstance(source, s.Items) and isinstance(kind, HashMapCursor):
        _map_cursor(low, stmt, source, kind)
    else:
        ctl.loop(low, stmt)


def _producer(low: Lowering, iterable: e.ShirExpr) -> Builtin:
    """The callee row of the producer of a direct walk."""
    resolved = low.typeck.callee_of(iterable.shir_id)
    if resolved is None or not isinstance(resolved.callee, Builtin):
        er.raise_internal_error("CE0000", span=iterable.loc,
                                detail="a direct walk with no producer reached SLIR")
    return resolved.callee


def _container(low: Lowering, producer: Builtin) -> p.Place:
    """The place of the container of a direct walk."""
    return views.reference_place(low, producer.recv, new_value=True)


def _cursor(low: Lowering, stmt: s.Loop, source: s.Items) -> None:
    """`Len` into a local, then the shape of a counted loop."""
    producer = _producer(low, source.iterable)
    base = _container(low, producer)
    count = arrays.length_of(low, base, low.type_of(producer.recv), stmt.loc)
    ctl.counted_walk(low, stmt, count, lambda at: _binds(source, p.Index(base, at)))


def _binds(source: s.Items, element: p.Place) -> tuple[ctl.Binds, ...]:
    """What the body writes first: the binder, with the value or the address of the place
    of its item."""
    if isinstance(source.binder, s.Discard):
        return ()
    value: r.Rvalue = (r.Ref(element, source.by.mode) if isinstance(source.by, s.Ref)
                       else r.Use(p.Copy(element)))
    return ((None, ((source.binder, value),)),)


def _map_cursor(low: Lowering, stmt: s.Loop, source: s.Items, kind: HashMapCursor) -> None:
    """The hidden cursor, then a header that steps it and switches on the tag of the answer,
    the body, which binds the payload, and the exit. The blocks are made in the order
    header, body, the block of the tag on the `None` edge, exit."""
    span = stmt.loc
    producer = _producer(low, source.iterable)
    owner = _container(low, producer)
    answer, some = _step_answer(low, source, span)
    stepped = low.new_local(answer, None, None, view=True)
    low.placement.temporary(stepped, False)
    low.emit([st.StorageLive(stepped, span)])
    low.walk_owners[stepped] = p.root_of(owner)
    cursor = low.temporary(r.Use(ctl.i32(0)), BuiltinType.I32, span)
    header = ctl.enter_header(low, stmt)
    among, at_header = ctl.header_state(low, stmt)
    low.placement.open_statement()
    method = replace(producer.method, name=f"{CURSOR_NEXT}{kind.view.name}")
    low.emit([st.Call(p.Local(stepped), st.Builtin(method, ()), (
        (p.Copy(owner), Convention.IN_GUARANTEED),
        (p.Copy(p.Local(cursor)), Convention.INOUT)), span)])
    tag = low.temporary(r.Discriminant(p.Local(stepped)), BuiltinType.I32, span)
    low.placement.detach(tag)
    low.emit(low.placement.close_scope(span))
    body, done, exit_ = low.new_block(), low.new_block(), low.new_block()
    payload: p.Place = p.Field(p.Downcast(p.Local(stepped), "Some"), 0)
    if isinstance(source.by, s.Ref):
        payload = p.Deref(payload)
    ctl.rounds(low, stmt, (p.Copy(p.Local(tag)), tag, span), body, header, done, exit_,
               (among, at_header), _binds(source, payload), leaves_at=1 - some)


def _step_answer(low: Lowering, source: s.Items, span: Span) -> tuple[EnumType, int]:
    """The type of the answer of a step, and the tag of its `Some`: `Maybe@(item)`, or
    `Maybe@(item*)` for a binder that points into the slot (ir.md 8.6)."""
    item = cast(IteratorType, deref_type(low.type_of(source.iterable))).element_type
    payload = PointerType(item) if isinstance(source.by, s.Ref) else item
    answer = ensure_maybe_type_in_table(low.tables.enums, payload,
                                        struct_table=low.tables.structs.by_name)
    some = answer.get_variant_index("Some") if isinstance(answer, EnumType) else None
    if not isinstance(answer, EnumType) or some is None:
        er.raise_internal_error("CE0000", span=span, detail="the answer of a step is no Maybe")
    return answer, some
