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
from sushi_lang.semantics.method_effects import answers_view
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.places import Step, walk_place
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
    never changes."""
    value = stmt.value
    region = low.answers.ownership.region_of(target.local)
    if region is None:
        er.raise_internal_error("CE0000", span=stmt.loc,
                                detail="a borrowed let with no borrow region reached SLIR")
    constant = isinstance(walk_place(value, Step.MEMBER | Step.INDEX).node, e.ConstRef)
    if not region.owners and not constant:
        low.holder = _held(low, value)
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
    owners = ([_owner(low, owner, stmt) for owner in region.owners] if region.owners
              else [] if constant else list(held))
    ident, _ = low.bind(target.local, stmt.loc, view=True)
    low.placement.declare(ident, False)
    low.emit([st.StorageLive(ident, stmt.loc)])
    low.emit(low.placement.begin_region(ident, owners, frozenset(region.until), stmt.loc))
    low.write(p.Local(ident), written, stmt.loc)


def _held(low: Lowering, value: e.ShirExpr) -> ShirId:
    """The node whose value a view of a `let` reads when no local owns it: the root of its
    place, through the receiver of each get-out."""
    node = walk_place(value, Step.MEMBER | Step.INDEX).node
    while isinstance(node, e.Call):
        resolved = low.typeck.callee_of(node.shir_id)
        if resolved is None or not (isinstance(resolved.callee, Builtin)
                                    and answers_view(resolved.callee.method)):
            break
        node = walk_place(resolved.callee.recv, Step.MEMBER | Step.INDEX).node
    if not isinstance(node, ShirNode) or isinstance(node, (e.LocalRef, e.StorageRef)):
        er.raise_internal_error("CE0000", span=value.loc,
                                detail="a borrowed let of a temporary with no root")
    return node.shir_id


def _owner(low: Lowering, owner: ShirLocalId | str, stmt: s.Assign) -> p.Place:
    """The place of an owner of a region row: a local. A `var` that owns stops in SLIR."""
    if isinstance(owner, str):
        raise StageStop(STAGE, "StorageRef owning", stmt.loc)
    return p.Local(low.local_of(owner, stmt.loc))


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
