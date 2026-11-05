"""SHIR to SLIR: the takes of an owning value (docs/design/ir.md 8.9, 8.13).

A marked field take moves the field and drops each other owning field of its receiver right
before the statement that consumes the take: the receiver is spent whole, and its own
`drop()` does not run. A `Take` with no `move` row
is its place, and the marker is inert there. A destructure of an owning value reads the row
of `borrow`: an OWNED value gives each binder and each place of a plain part the `Copy` of
it first, then drops each owning part that `_` discards, then gives each binder and each
place of an owning part the `Move` of it, and is spent whole; a BORROWED value gives
each binder of an owning part a view local in a region over the owners of the value, and a
plain part a `Copy`. Each arm is a free function that takes the `Lowering` of `lower.py`
first. This module reads `lower.py` for its types only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.tuples import tuple_elements
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import lower_views as views
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.lower_values import Value
from sushi_lang.semantics.typesys import StructType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering

#: A place that a destructure reads or writes.
_Place = e.LocalRef | e.StorageRef | e.Field | e.Index | e.Slice

#: One target of a destructure with its path, and the type of its element.
_Target = tuple[tuple[int, ...], Optional[ShirLocalId | s.Place], Type]


def take_value(low: Lowering, node: e.Take) -> Value:
    """`nom s.f` in a `let` or a `return`: the field take with a `move` row, else its
    place."""
    if low.answers.ownership.ownership_of(node.shir_id) is Ownership.MOVE:
        return r.Use(take_field(low, node.place))
    return low.value(node.place)


def take_field(low: Lowering, place: e.ShirExpr) -> p.Move:
    """The field take of `place`, one step off a local: the `Move` of the field, which spends
    the whole local. The `Drop` of each other owning field of the local waits in `takes`
    for the statement that consumes the `Move` (`Lowering.write`), so an exit before it
    drops the local whole."""
    if not (isinstance(place, e.Field) and isinstance(place.base, e.LocalRef)):
        er.raise_internal_error("CE0000", span=place.loc,
                                detail="a field take of no field of a local reached SLIR")
    root = p.Local(low.local_of(place.base.local, place.base.loc))
    receiver = low.type_of(place.base)
    index = low.typeck.field_index_of(place.shir_id)
    if not isinstance(receiver, StructType) or index is None:
        er.raise_internal_error("CE0000", span=place.loc,
                                detail="a field take of no struct field reached SLIR")
    taken = p.Field(root, index)
    low.takes[taken] = tuple(p.Field(root, other) for other, (_name, ty)
                             in enumerate(receiver.fields) if other != index and low.owns(ty))
    return p.Move(taken)


def destructure(low: Lowering, stmt: s.Destructure) -> None:
    """A destructure of a value that owns (ir.md 8.9), by the row of `borrow` at its
    value."""
    ty = low.type_of(stmt.value)
    targets = _flatten(stmt.targets, ty, ())
    if low.answers.ownership.ownership_of(stmt.value.shir_id) is Ownership.REJECT:
        _views(low, stmt, targets)
        return
    moves = any(target is not None and low.owns(element) for _, target, element in targets)
    rebinds = any(isinstance(target, s.Place) for _, target, _ in targets)
    source: p.Place
    if (moves and not rebinds and isinstance(stmt.value, e.LocalRef)
            and low.answers.ownership.ownership_of(stmt.value.shir_id) is Ownership.MOVE):
        source = agg.place_of(low, cast(_Place, stmt.value))
    else:
        source = p.Local(low.temporary(low.value(stmt.value), ty, stmt.value.loc))
    places = {path: _place(low, target.target) for path, target, _ in targets
              if isinstance(target, s.Place)}
    plain = [path for path, target, element in targets
             if target is not None and not low.owns(element)]
    for path in plain:
        _write(low, places.get(path), targets, path, p.Copy(_part(source, path)), stmt.loc)
    if moves:
        for path, target, element in targets:
            if target is None and low.owns(element):
                low.emit(low.placement.drop_part(_part(source, path), stmt.loc))
    for path, target, element in targets:
        if target is not None and low.owns(element):
            _write(low, places.get(path), targets, path, p.Move(_part(source, path)),
                   stmt.loc)
    if moves:
        low.placement.move(_local(source), stmt.loc)


def _place(low: Lowering, target: e.ShirExpr) -> p.Place:
    """The place of a target of a rebind form, its indexes and their checks first: every
    place is evaluated before the first part moves."""
    if isinstance(target, e.LocalRef):
        return p.Local(low.local_of(target.local, target.loc))
    return agg.place_of(low, cast(_Place, target))


def _write(low: Lowering, place: Optional[p.Place], targets: list[_Target],
           path: tuple[int, ...], read: p.Copy | p.Move, span: Span) -> None:
    """The write of one element: into the place of a rebind form, else a new binder."""
    if place is not None:
        _rebind(low, place, read, span)
        return
    binder = next(target for at, target, _ in targets if at == path)
    if not isinstance(binder, ShirLocalId):
        er.raise_internal_error("CE0000", span=span, detail="a destructure target of no kind")
    _binder(low, binder, read, span)


def _flatten(targets: Sequence[s.DestructureTarget], ty: Type,
             prefix: tuple[int, ...]) -> list[_Target]:
    """Each leaf target with its path and the type of its element, from left to right; a
    `_` is None."""
    found: list[_Target] = []
    for index, (target, element) in enumerate(zip(targets, tuple_elements(ty), strict=True)):
        path = (*prefix, index)
        if isinstance(target, s.Nested):
            found.extend(_flatten(target.targets, element, path))
        else:
            found.append((path, None if isinstance(target, s.Discard) else target, element))
    return found


def _part(source: p.Place, path: tuple[int, ...]) -> p.Field:
    """The part of `source` at the path `path`, which is never empty."""
    part = p.Field(source, path[0])
    for index in path[1:]:
        part = p.Field(part, index)
    return part


def _local(place: p.Place) -> p.LocalId:
    root = p.root_of(place)
    if not isinstance(root, p.Local):
        er.raise_internal_error("CE0000", detail="a destructure takes from no local")
    return root.id


def _binder(low: Lowering, local: ShirLocalId, read: p.Copy | p.Move, span: Span) -> None:
    """A new local of the block, written with its element. An owning one takes the
    element with the `Move` of its part, and its flag follows its write."""
    ident, ty = low.bind(local, span)
    owning = low.owns(ty)
    if owning:
        low.give_flag(ident)
    low.placement.declare(ident, owning)
    low.emit([st.StorageLive(ident, span), st.Assign(p.Local(ident), r.Use(read), span)])
    low.emit(low.placement.flag_declared(ident, span))


def _rebind(low: Lowering, place: p.Place, read: p.Copy | p.Move, span: Span) -> None:
    """A place of a rebind form takes its element: a local drops its old value first and is
    live again (rule 6), a field or an element drops its old part first. A plain one is
    written. A borrowed parameter that owns stops (`borrowed rebind`), as its rebind does."""
    if not isinstance(read, p.Move):
        low.write(place, r.Use(read), span)
        return
    if isinstance(place, p.Local):
        ident = place.id
        if low.placement.is_borrowed(ident) or low.placement.is_address(ident):
            raise StageStop(STAGE, "borrowed rebind", span)
        low.emit(low.placement.drop_old_value(ident, span))
        low.emit([st.Assign(place, r.Use(read), span)])
        low.emit(low.placement.flag_stored(ident, span))
        return
    if not isinstance(place, (p.Field, p.Index)):
        er.raise_internal_error("CE0000", span=span, detail="a part write of no part")
    low.emit(low.placement.drop_part(place, span))
    low.emit([st.Assign(place, r.Use(read), span)])


def _views(low: Lowering, stmt: s.Destructure, targets: list[_Target]) -> None:
    """A destructure of a borrowed value: the value is read where it is, a view of a
    temporary keeps the temporary in a hidden owner local of the block, and each binder of
    an owning part is a view local in its region; a plain part is a `Copy`."""
    value = stmt.value.place if isinstance(stmt.value, e.Take) else stmt.value
    root = walk_place(value, Step.MEMBER | Step.INDEX).node
    if not isinstance(root, (e.LocalRef, e.StorageRef, e.ConstRef)):
        low.holder = views.held_root(low, value)
    try:
        source = agg.place_of(low, cast(_Place, value))
    finally:
        low.holder = None
    for path, target, element in targets:
        read = p.Copy(_part(source, path))
        if isinstance(target, s.Place):
            low.write(agg.place_of(low, cast(_Place, target.target)), r.Use(read), stmt.loc)
        elif target is not None and low.owns(element):
            views.bind_part_view(low, target, read, p.root_of(source), stmt.loc)
        elif target is not None:
            _binder(low, target, read, stmt.loc)
