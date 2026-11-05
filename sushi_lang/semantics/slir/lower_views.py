"""SHIR to SLIR: the view locals and their borrow regions (docs/design/ir.md 8.2, 8.6, 8.9).

A borrowed view is a shallow `Copy` of a value that an owner keeps, in a VIEW local: it
never drops, never moves and is never an `Owned` argument. A `let` with a `REJECT` row at
its value makes a view local, and its region is the region row of `borrow`: one
`BeginBorrow` for each owner at the write, and one `EndBorrow` for each after the statement
of each last use. An arm of an `if` or a `match` with no last use in it ends the region at
its top, when the branch holds a last use (a last use in the condition, or in another arm);
an `if` with no `else` ends it on its zero edge. An arm of a `match` ends the region of a
temporary view of its scrutinee at its top, after its binders. An exit ends each region that it crosses
(`drops.py`). A view that goes into a temporary (the answer of a get-out, the payload of a
`??` over a borrowed wrapper) has a region over the root of the place that it reads, which
ends with the storage of the temporary. Each arm is a free function that takes the
`Lowering` of `lower.py` first. This module reads `lower.py` for its types only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.borrowed_return import RECEIVER_POSITION
from sushi_lang.semantics.method_effects import answers_view
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.places import Step, is_get_call, walk_place
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Builtin
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock, ShirNode
from sushi_lang.semantics.shir.walk import walk
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.drops import OpenRegion
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.lower_values import CallValue, ViewValue

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def let_view(low: Lowering, stmt: s.Assign, target: e.LocalRef) -> None:
    """`let T v = <borrowed value>`: a view local, written in its region (ir.md 8.9). A view
    whose owner is a temporary keeps it: the temporary is a hidden owner local of the block,
    and the region is over it. A view of a constant has no owner and no region: a constant
    never changes. The answer of a call with a `peek` return is in a region over the owners
    of its region row; a source that is a literal has none. A `Take` here is no field take: it is its place."""
    value = stmt.value.place if isinstance(stmt.value, e.Take) else stmt.value
    region = low.answers.ownership.region_of(target.local)
    if region is None:
        er.raise_internal_error("CE0000", span=stmt.loc,
                                detail="a borrowed let with no borrow region reached SLIR")
    constant = isinstance(walk_place(value, Step.MEMBER | Step.INDEX).node, e.ConstRef)
    called = walk_place(value, Step.TRY).node
    borrowed = isinstance(called, e.Call) and low.typeck.sources_of(called.shir_id) is not None
    if not region.owners and not constant and not borrowed:
        low.holder = held_root(low, value)
    written: r.Use | CallValue
    held: tuple[p.Place, ...]
    try:
        if isinstance(value, (e.LocalRef, e.Field, e.Index, e.Slice)):
            read = p.Copy(agg.place_of(low, value))
            written, held = r.Use(read), (p.root_of(read.place),)
        elif isinstance(value, (e.Call, e.Try)):
            lowered = low.value(value)
            if not isinstance(lowered, ViewValue):
                er.raise_internal_error("CE0000", span=value.loc,
                                        detail="a borrowed let of a value that is no view")
            written, held = lowered.value, lowered.owners
        else:
            dispatch.stop(STAGE, value, state="borrowed")
    finally:
        low.holder = None
    owners = ([_owner(low, owner, stmt.loc) for owner in region.owners] if region.owners
              else [] if constant or borrowed else list(held))
    ident, _ = low.bind(target.local, stmt.loc, view=True)
    low.placement.declare(ident, False)
    low.emit([st.StorageLive(ident, stmt.loc)])
    low.emit(low.placement.begin_region(ident, owners, frozenset(region.until), stmt.loc))
    low.write(p.Local(ident), written, stmt.loc)


def held_root(low: Lowering, value: e.ShirExpr) -> ShirId:
    """The node whose value a view of a `let` reads when no local owns it: the root of its
    place, through each `??`, the receiver of each get-out and the receiver of each
    read-through `or_err` (a `reject` row at its receiver)."""
    def crosses(call: e.ShirExpr) -> bool:
        resolved = low.typeck.callee_of(call.shir_id)
        return resolved is not None and isinstance(resolved.callee, Builtin) and (
            answers_view(resolved.callee.method)
            or low.answers.ownership.ownership_of(resolved.callee.recv.shir_id)
            is Ownership.REJECT)

    node = walk_place(value, Step.MEMBER | Step.INDEX | Step.TRY | Step.CALL,
                      crosses_call=crosses).node
    if not isinstance(node, ShirNode) or isinstance(node, (e.LocalRef, e.StorageRef)):
        er.raise_internal_error("CE0000", span=value.loc,
                                detail="a borrowed let of a temporary with no root")
    return node.shir_id


def _owner(low: Lowering, owner: ShirLocalId | str, span: Span) -> p.Place:
    """The place of an owner of a region row: a local. A `var` that owns stops in SLIR."""
    if isinstance(owner, str):
        raise StageStop(STAGE, "StorageRef owning", span)
    return p.Local(low.local_of(owner, span))


def binds_a_view(low: Lowering, local: ShirLocalId) -> bool:
    """Whether a bare binder of a pattern binds a payload that owns: a view local."""
    ty = low.typeck.local_type(local)
    return ty is not None and low.owns(ty)


def bind_view(low: Lowering, local: ShirLocalId, value: r.Rvalue,
              span: Span) -> list[st.SlirStatement]:
    """A bare binder of an owning payload (ir.md 8.9): a view local of the arm, written with
    the `Copy` of its payload place, in a region over the root of that place to the last
    uses of the region row of `borrow`. A binder with no use has no region."""
    region = low.answers.ownership.region_of(local)
    if region is None or not (isinstance(value, r.Use) and isinstance(value.operand, p.Copy)):
        er.raise_internal_error("CE0000", span=span,
                                detail="a binder of an owning payload with no borrow region "
                                       "reached SLIR")
    ident, _ = low.bind(local, span, view=True)
    low.placement.declare(ident, False)
    statements: list[st.SlirStatement] = [st.StorageLive(ident, span)]
    if region.until:
        statements += low.placement.begin_region(
            ident, [p.root_of(value.operand.place)], frozenset(region.until), span)
    return statements + [st.Assign(p.Local(ident), value, span)]


def bind_part_view(low: Lowering, local: ShirLocalId, read: p.Copy, root: p.Place,
                   span: Span) -> None:
    """A binder of an owning part of a borrowed value (ir.md 8.9): a view local of the
    block, written with the `Copy` of its part place, in a region over the owners of its
    region row, or over the `root` of the place when no local owns the value."""
    region = low.answers.ownership.region_of(local)
    if region is None:
        er.raise_internal_error("CE0000", span=span,
                                detail="a view binder of a destructure with no borrow region "
                                       "reached SLIR")
    owners = [_owner(low, owner, span) for owner in region.owners] or [root]
    ident, _ = low.bind(local, span, view=True)
    low.placement.declare(ident, False)
    low.emit([st.StorageLive(ident, span)])
    low.emit(low.placement.begin_region(ident, owners, frozenset(region.until), span))
    low.emit([st.Assign(p.Local(ident), r.Use(read), span)])


def bind_address(low: Lowering, local: ShirLocalId, value: r.Ref,
                 span: Span) -> list[st.SlirStatement]:
    """A `peek` or `poke` binder of the pointee of an `Own(...)` (ir.md 8.2): an address
    local of the arm, written with the `Ref` of its payload place, in a region over the root
    of that place with the mode of the `Ref`, to the last uses of the region row of
    `borrow`. A binder with no use has no region."""
    region = low.answers.ownership.region_of(local)
    if region is None:
        er.raise_internal_error("CE0000", span=span,
                                detail="a reference binder with no borrow region reached SLIR")
    ident, _ = low.bind(local, span)
    low.placement.address(ident)
    statements: list[st.SlirStatement] = [st.StorageLive(ident, span)]
    if region.until:
        statements += low.placement.begin_region(
            ident, [p.root_of(value.place)], frozenset(region.until), span, value.mode)
    return statements + [st.Assign(p.Local(ident), value, span)]


def let_reference(low: Lowering, stmt: s.Assign, target: e.LocalRef) -> None:
    """`let peek T x = <place>` / `let poke T x = <place>` (ir.md 8.9): an address local of
    the block, written with the `Ref` of its place, in a region over the root of the place
    with the mode of the `Ref`, to the last uses of the region row of `borrow`."""
    borrow = stmt.value
    region = low.answers.ownership.region_of(target.local)
    if not isinstance(borrow, e.Borrow) or region is None:
        er.raise_internal_error("CE0000", span=stmt.loc,
                                detail="a reference let with no borrow region reached SLIR")
    place = reference_place(low, borrow.place)
    ident, _ = low.bind(target.local, stmt.loc)
    low.placement.address(ident)
    low.emit([st.StorageLive(ident, stmt.loc)])
    low.emit(low.placement.begin_region(ident, [p.root_of(place)], frozenset(region.until),
                                        stmt.loc, borrow.mode))
    low.emit([st.Assign(p.Local(ident), r.Ref(place, borrow.mode), stmt.loc)])


def reference_place(low: Lowering, expr: e.ShirExpr) -> p.Place:
    """The place that a reference points into: a local or a `var`, a field or an element of
    one, the pointee of an `Own` (`o.get()`, `Deref(Field(place, 0))`), or a part of a
    constant, whose value a hidden local of the block holds (ir.md 8.9)."""
    walked = walk_place(expr, Step.MEMBER | Step.INDEX | Step.CALL, crosses_call=is_get_call)
    root = walked.node
    place: p.Place
    if isinstance(root, (e.LocalRef, e.StorageRef)):
        place = agg.place_of(low, root)
    elif isinstance(root, e.ConstRef):
        place = p.Local(low.temporary(r.Use(p.Const(low.constant(root))), low.type_of(root),
                                      root.loc, held=True))
    else:
        er.raise_internal_error("CE0000", span=expr.loc,
                                detail="a reference into no place reached SLIR")
    for step in reversed(walked.path):
        if isinstance(step, e.Call):
            place = p.Deref(p.Field(place, 0))
        else:
            place = agg.project(low, place, [step])
    return place


def view_of_sources(value: CallValue, sources: tuple[int, ...], method: bool) -> ViewValue:
    """The answer of a call with a `peek` return (ir.md 8.9): a view over the root of the
    operand of each argument at a position of the source row, the receiver of a method
    first. A constant has no owner."""
    offset = 1 if method else 0
    operands = [value.args[0 if position == RECEIVER_POSITION else position + offset][0]
                for position in sources]
    owners = [p.root_of(operand.place) for operand in operands
              if isinstance(operand, (p.Copy, p.Move))]
    return ViewValue(value, tuple(dict.fromkeys(owners)))


def view_of(value: r.Use | CallValue, read: p.Operand) -> ViewValue:
    """A view of what `read` reads, over the root of its place. A constant has no owner, so
    a view of one is an internal error."""
    if isinstance(read, p.Const):
        er.raise_internal_error("CE0000", detail="a view of a constant reached SLIR")
    return ViewValue(value, (p.root_of(read.place),))


def wrapper(low: Lowering, inner: e.ShirExpr) -> tuple[p.Place, bool]:
    """The place of the wrapper that a `??` reads, and whether the `??` reads through it: a
    borrowed wrapper (a `reject` row) is read where it is, and a wrapper that is a view (a
    nested `??`) is read through too. Any other wrapper goes into a temporary, which the
    `??` takes."""
    if (low.answers.ownership.ownership_of(inner.shir_id) is Ownership.REJECT
            and isinstance(inner, (e.LocalRef, e.Field, e.Index))):
        return agg.place_of(low, inner), True
    lowered = low.value(inner)
    if (isinstance(lowered, ViewValue) and isinstance(lowered.value, r.Use)
            and isinstance(lowered.value.operand, p.Copy)):
        return lowered.value.operand.place, True
    return p.Local(low.temporary(lowered, low.type_of(inner), inner.loc)), isinstance(
        lowered, ViewValue)


def receiver_of(low: Lowering, call: e.ShirExpr) -> e.ShirExpr:
    """The receiver of a call of a built-in method. Any other node is an internal error."""
    resolved = low.typeck.callee_of(call.shir_id) if isinstance(call, e.Call) else None
    if resolved is None or not isinstance(resolved.callee, Builtin):
        er.raise_internal_error("CE0000", span=call.loc,
                                detail="a built-in receiver of a node that is no method call")
    return resolved.callee.recv


def read_through(low: Lowering, inner: e.ShirExpr) -> Optional[tuple[p.Place, p.Operand]]:
    """The operand of a `??` that is `m.or_err(nom e)` over a borrowed `Maybe` (a `reject`
    row at the receiver, ir.md 8.9): the place of the `Maybe`, read where it is (`wrapper`),
    and the error operand, evaluated after it. A named error goes into a temporary of the
    statement, so the `None` path moves the temporary and the `Some` path drops it. None
    for any other operand."""
    if not isinstance(inner, e.Call):
        return None
    resolved = low.typeck.callee_of(inner.shir_id)
    if resolved is None or not isinstance(resolved.callee, Builtin):
        return None
    receiver = resolved.callee.recv
    if low.answers.ownership.ownership_of(receiver.shir_id) is not Ownership.REJECT:
        return None
    place, _borrowed = wrapper(low, receiver)
    value = inner.args[0].value
    error = low.operand(value)
    if isinstance(error, p.Move) and isinstance(value, e.LocalRef):
        error = p.Move(p.Local(low.temporary(r.Use(error), low.type_of(value), value.loc)))
    return place, error


def after(low: Lowering, stmt: s.ShirStmt) -> list[st.SlirStatement]:
    """The `EndBorrow` of each region whose last use is the statement `stmt`."""
    return low.placement.end_regions(lambda region: stmt.shir_id in region.until, stmt.loc)


def ends_at_arm(low: Lowering, branch: ShirNode, body: Optional[ShirBlock]) -> bool:
    """Whether an arm of `branch` (None for the zero edge of an `if` with no `else`) ends a
    region at its top."""
    return bool(_ending(low, branch, body))


def enter_arm(low: Lowering, branch: ShirNode, body: Optional[ShirBlock]) -> None:
    """At the top of an arm of `branch`: the end of each region whose last use is in the
    branch and not in the arm."""
    ending = _ending(low, branch, body)
    if ending:
        low.emit(low.placement.end_regions(lambda region: region in ending, branch.loc))


def _ending(low: Lowering, branch: ShirNode, body: Optional[ShirBlock]) -> list[OpenRegion]:
    """The regions that an arm of `branch` ends at its top. A temporary view of a `match` is
    its scrutinee: the arm reads it no more after its binders."""
    regions = low.placement.open_regions()
    ending = [region for region in regions if not region.until and isinstance(branch, s.Match)]
    if any(region.until for region in regions):
        inside, here = _ids(branch), _ids(body)
        ending += [region for region in regions
                   if region.until & inside and not region.until & here]
    return ending


def _ids(node: Optional[ShirNode]) -> frozenset[ShirId]:
    if node is None:
        return frozenset()
    return frozenset(value.shir_id for _path, value in walk(node, "")
                     if isinstance(value, ShirNode))
