"""SHIR to SLIR: the direct walks and the iterator values (docs/design/ir.md 7.7, 8.6, 8.7, 8.9).

A `Cursor` is `Len` into a local, then the shape of a counted loop: the binder reads the
element at the counter. A `HashMapCursor` has a hidden cursor local, and each round a `Call`
of the `cursor_next` row of its view writes a view local of a `Maybe`; a switch on its tag
leaves the loop or goes to the body, and the binder reads the payload. SLIR does not see the
buckets. A `continue` goes to the header.

A bare binder of an owning item is a view local, and a `peek` or a `poke` binder is an address
local (`lower_control._bind`), in a region over the root of the container. The container
is the place of its receiver, or a new value in a temporary of the loop statement, which
drops at the end of the statement. The loop statement holds the cursor and the counter too.

A producer, a range and `Iterator.new` as a value are ONE `Aggregate(AggIterator)`, with the
row of the source and the heap decision of `borrow`. An `IteratorValue` walk and a `Protocol`
walk hold the iterator in a temporary of the loop statement, which drops by the rules of a
temporary (8.10, rule 7), or walk it where it is. Each round is an `Indirect` call through
`next_fn`, or a `Direct` call of the `next()` of the type, a switch on the tag of the `Maybe`,
and the body. An item that the walk owns moves into the binder, and the `None` edge drops the
`Maybe`. A lent item is a view of the `Maybe`.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.iterators import item_parts
from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.method_effects import CURSOR_NEXT
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Builtin, Method
from sushi_lang.semantics.shir.loop_kind import (
    HASHMAP_VIEWS, Cursor, HashMapCursor, IteratorValue, Protocol)
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import lower_arrays as arrays
from sushi_lang.semantics.slir import lower_calls as calls
from sushi_lang.semantics.slir import lower_control as ctl
from sushi_lang.semantics.slir import lower_views as views
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.typesys import (
    BuiltinType, EnumType, FunctionType, IteratorType, PointerType, Type, deref_type,
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
    elif isinstance(source, s.Items) and isinstance(kind, (IteratorValue, Protocol)):
        _value_walk(low, stmt, source, kind)
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
    return _maybe_of(low, PointerType(item) if isinstance(source.by, s.Ref) else item, span)


def _maybe_of(low: Lowering, payload: Type, span: Span) -> tuple[EnumType, int]:
    answer = ensure_maybe_type_in_table(low.tables.enums, payload,
                                        struct_table=low.tables.structs.by_name)
    some = answer.get_variant_index("Some") if isinstance(answer, EnumType) else None
    if not isinstance(answer, EnumType) or some is None:
        er.raise_internal_error("CE0000", span=span, detail="the answer of a step is no Maybe")
    return answer, some


def producer_value(low: Lowering, node: e.Call, callee: Builtin) -> r.Aggregate:
    """A producer as a value. A lent one reads the place of its container, and an owned one
    moves the temporary into its state."""
    ownership = low.answers.ownership
    lent = ownership.lent_of(node.shir_id)
    if lent is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="an iterator producer with no state reached SLIR")
    heap = not lent or bool(ownership.lent_on_heap(node.shir_id))
    part = (p.Copy(views.reference_place(low, callee.recv)) if lent
            else low.operand(callee.recv))
    container = cast(Type, deref_type(low.type_of(callee.recv)))
    view = HASHMAP_VIEWS.get(callee.method.name)
    source: r.IterSource = (r.IterElements(container, lent) if view is None
                            else r.IterMapView(container, view.name, lent))
    return r.Aggregate(r.AggIterator(low.type_of(node), source, heap), (part,))


def adapter_value(low: Lowering, node: e.Call, callee: Builtin) -> r.Aggregate:
    """A lazy adapter as a value: the move of its receiver, then the move of each argument,
    with the heap decision of `borrow`. A lent adapter reads the same answer as each other
    link of its chain."""
    ownership = low.answers.ownership
    lent = ownership.lent_of(node.shir_id)
    if lent is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="an iterator adapter with no state reached SLIR")
    heap = not lent or bool(ownership.lent_on_heap(node.shir_id))
    parts = (calls.receiver(low, callee, node)[0][0],
             *(calls.argument(low, node, index, arg)[0] for index, arg in enumerate(node.args)))
    source = _iterator_type(low, callee.recv)
    argument = deref_type(low.type_of(node.args[0].value)) if node.args else None
    row = _adapter_row(callee.method.name, source, argument, node.loc)
    return r.Aggregate(r.AggIterator(low.type_of(node), row, heap), parts)


def _iterator_type(low: Lowering, value: e.ShirExpr) -> IteratorType:
    ty = deref_type(low.type_of(value))
    if not isinstance(ty, IteratorType):
        er.raise_internal_error("CE0000", span=value.loc,
                                detail=f"an adapter over {display_type(ty)} reached SLIR")
    return ty


def _adapter_row(name: str, source: IteratorType, argument: Type | None,
                 span: Span) -> r.IterAdapter:
    """The row of the source table of a lazy adapter (ir.md 8.7)."""
    if name in ("map", "filter") and isinstance(argument, FunctionType):
        return (r.IterMap if name == "map" else r.IterFilter)(source, argument)
    if name == "zip" and isinstance(argument, IteratorType):
        return r.IterZip(source, argument)
    if name == "enumerate" and argument is None:
        return r.IterEnumerate(source)
    if name == "clone_each" and argument is None:
        return r.IterCloneEach(source)
    er.raise_internal_error("CE0000", span=span,
                            detail=f"the adapter '{name}' with no row reached SLIR")


def range_value(low: Lowering, node: e.Range) -> r.Aggregate:
    """A range as a value (ir.md 8.9): an owned iterator, with its first value and its count."""
    if not low.typeck.range_value_of(node.shir_id):
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a range that is no value reached SLIR as a value")
    start, end = ctl.bound(low, node.start), ctl.bound(low, node.end)
    first, count = ctl.range_operands(low, node, start, end)
    source = r.IterRange(-1 if node.reverse else 1)
    return r.Aggregate(r.AggIterator(low.type_of(node), source, True), (first, count))


def new_value(low: Lowering, node: e.Call, next_: st.SlirCallee) -> r.Aggregate:
    """`Iterator.new(nom x)`: an owned iterator, with the `next()` of `x`."""
    next_row = low.typeck.iterator_next_of(node.shir_id)
    if next_row is None or not isinstance(next_, st.Direct):
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="an Iterator.new with no next() reached SLIR")
    value = node.args[0].value
    source = r.IterNew(cast(Type, deref_type(low.type_of(value))), next_.symbol,
                       cast(Method, next_row.callee).receiver)
    return r.Aggregate(r.AggIterator(low.type_of(node), source, True), (low.operand(value),))


def _value_walk(low: Lowering, stmt: s.Loop, source: s.Items,
                kind: IteratorValue | Protocol) -> None:
    """The iterator, then a header that calls `next_fn`, or the `next()` of a `Protocol` walk
    with the iterator by the convention of its `self`, and switches on the tag of the answer,
    the body, and the exit. The blocks are made in the order header, body, the block of the
    tag on the `None` edge, exit."""
    span = stmt.loc
    iterator = _walked_iterator(low, stmt, source.iterable)
    target: st.SlirCallee
    owned_parts: tuple[tuple[int, Type], ...] = ()
    if isinstance(kind, Protocol):
        method = cast(Method, kind.next.callee)
        item, peeks = kind.item, False
        target = calls.user_method(low, method, kind.next, span)
        args: tuple[tuple[p.Operand, Convention], ...] = (
            (p.Copy(iterator), method.receiver),)
    else:
        ty = cast(IteratorType, deref_type(low.type_of(source.iterable)))
        item, peeks = ty.element_type, bool(ty.peeks)
        target, args = st.Indirect(p.Copy(iterator)), ()
        owned_parts = _owned_parts(low, ty)
    answer, some = _maybe_of(low, item, span)
    takes_item = low.owns(item) and not peeks
    stepped = low.new_local(answer, None, None,
                            view=low.owns(item) and not takes_item and not owned_parts)
    low.placement.temporary(stepped, False)
    low.emit([st.StorageLive(stepped, span)])
    low.walk_owners[stepped] = p.root_of(iterator)
    header = ctl.enter_header(low, stmt)
    among, at_header = ctl.header_state(low, stmt)
    low.placement.open_statement()
    low.emit([st.Call(p.Local(stepped), target, args, span)])
    tag = low.temporary(r.Discriminant(p.Local(stepped)), BuiltinType.I32, span)
    low.placement.detach(tag)
    low.emit(low.placement.close_scope(span))
    body, done, exit_ = low.new_block(), low.new_block(), low.new_block()
    if takes_item or owned_parts:
        done.statements.append(low.placement.drop_step(stepped, span))
    payload: p.Place = p.Field(p.Downcast(p.Local(stepped), "Some"), 0)
    binds: tuple[ctl.Binds, ...] = ()
    if not isinstance(source.binder, s.Discard):
        read = p.Move(payload) if takes_item else p.Copy(payload)
        binds = ((None, ((source.binder, r.Use(read)),)),)
    elif takes_item:
        body.statements.append(low.placement.drop_step(stepped, span))
    held = tuple((p.Field(payload, index), part) for index, part in owned_parts)
    ctl.rounds(low, stmt, (p.Copy(p.Local(tag)), tag, span), body, header, done, exit_,
               (among, at_header), binds, leaves_at=1 - some, held=held)


def _owned_parts(low: Lowering, ty: IteratorType) -> tuple[tuple[int, Type], ...]:
    """The owning parts of a tuple item whose parts differ in mode: the round holds each one
    in a hidden owner local, and the binder views the item."""
    if len(ty.peeks) <= 1:
        return ()
    return tuple((index, part) for index, (part, lent) in enumerate(
        zip(item_parts(ty), ty.peeks, strict=True)) if not lent and low.owns(part))


def _walked_iterator(low: Lowering, stmt: s.Loop, iterable: e.ShirExpr) -> p.Place:
    """The place of the iterator of a walk: where it is for a walk in place, else a temporary
    of the loop statement, which takes the value."""
    if low.answers.ownership.in_place_of(stmt.shir_id):
        return agg.place_of(low, cast(e.LocalRef, iterable))
    return p.Local(low.temporary(r.Use(low.operand(iterable)), low.type_of(iterable), stmt.loc))
