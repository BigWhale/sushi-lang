"""The region rules and the view-local rules of the SLIR verifier (docs/design/ir.md 8.6, 8.13).

A view local (8.2) is written by a `Copy` of a place, or by a call whose answer is a view,
by one statement on each path (`_one_write_on_each_path`). The `_0` of a
function with a `peek` return is a view local too. A view local is never
the place of a `Drop` or a `DropIfSet`, never `Move`d and never the operand of an `Owned`
argument, at its root or at a projection of it. At each read of a view local, the regions
that name it are open; a region has one view, and it is a view local. One `Ref` writes an
address local on each path (8.2), and it is never dropped as a whole and never moved. The `Ref` comes
before each use of the address local on every path (a dataflow: a block knows the address
locals that each path into it wrote), and at each use a region over the root of the place of
each of its `Ref`s is open. Two paths that meet can bring one region over two owners.

A borrow region is a `BeginBorrow` of an owner and the `EndBorrow` of its id. A dataflow over
the CFG gives each block the set of open regions at its top: every path into a block brings
the same set, a region begins one time and ends one time on a path, and no region is open
at a `return`. A `Trap` ends the program, so it closes nothing. While a region over an owner
is open, no statement writes that owner, moves it, drops it or gives it to an `Inout`
argument, at its root or at a projection of it. A change of the owner after the `EndBorrow`
is legal. An address local is the place of a `Drop` as a whole only at a rebind through
it: the next statement of its block writes the local (8.10, rules 6 and 8).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterator, NoReturn, Optional, Sequence

from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import SlirBlock, SlirFunction, address_locals
from sushi_lang.semantics.slir.ids import BlockId, BorrowId, LocalId
from sushi_lang.semantics.slir.printer import show
from sushi_lang.semantics.slir.verify_flow import successors
from sushi_lang.semantics.slir.verify_rules import (
    ADDRESS_LOCAL, REGION, REGION_OWNER, VIEW_LOCAL)

if TYPE_CHECKING:
    from typing import TypeAlias

    Fail: TypeAlias = Callable[[str, str, Optional[Span]], NoReturn]
    _Open: TypeAlias = dict[BorrowId, frozenset[p.Place]]


def check_regions(function: SlirFunction, fail: Fail) -> None:
    """CE0158, through `fail`, at the first view-local rule or region rule that `function`
    breaks."""
    views = {local.id for local in function.locals if local.view}
    if views:
        _view_locals(function, views, fail)
    regions = _regions_of_views(function, views, fail)
    addresses = address_locals(function)
    refs: dict[LocalId, frozenset[p.Place]] = {}
    if addresses:
        refs = _address_locals(function, addresses, fail)
        _written_before_use(function, addresses, fail)
    if views or addresses:
        _one_write_on_each_path(function, frozenset(views), addresses, fail)
    blocks = {block.id: block for block in function.blocks}
    at_top: dict[BlockId, _Open] = {function.entry: {}}
    work = [function.entry]
    while work:
        block = blocks[work.pop()]
        open_ = _through(block, dict(at_top[block.id]), fail, refs, regions, report=False)
        if isinstance(block.terminator, tm.Return) and open_:
            fail(REGION, f"the regions {_names(open_)} are open at the return",
                 block.terminator.span)
        for target in successors(block.terminator):
            known = at_top.get(target)
            if known is None:
                at_top[target] = open_
                work.append(target)
            elif known.keys() != open_.keys():
                fail(REGION, f"bb{target.index} is reached with the regions {_names(known)} "
                             f"open on one path and {_names(open_)} on another",
                     block.terminator.span)
            else:
                met = {ident: known[ident] | open_[ident] for ident in known}
                if met != known:
                    at_top[target] = met
                    work.append(target)
    for block in function.blocks:
        if block.id in at_top:
            _through(block, dict(at_top[block.id]), fail, refs, regions, report=True)


def _through(block: SlirBlock, open_: _Open, fail: Fail,
             refs: dict[LocalId, frozenset[p.Place]],
             regions: dict[LocalId, frozenset[BorrowId]], *, report: bool) -> _Open:
    """The open regions after `block`. With `report`, after the dataflow has its answer at
    the top of each block, the checks of the uses and of the owners in the regions too."""
    for stmt in block.statements:
        if report:
            _address_in_region(stmt, open_, refs, fail)
            _view_in_region(stmt, open_, regions, fail)
        if isinstance(stmt, st.BeginBorrow):
            if stmt.id in open_:
                fail(REGION, f"the region b{stmt.id.index} begins again while it is open",
                     stmt.span)
            open_[stmt.id] = frozenset({p.root_of(stmt.place)})
        elif isinstance(stmt, st.EndBorrow):
            if open_.pop(stmt.id, None) is None:
                fail(REGION, f"the region b{stmt.id.index} ends, and it is not open", stmt.span)
        elif report:
            _owners_kept(stmt, open_, fail)
    if report:
        _address_in_region(block.terminator, open_, refs, fail)
        _view_in_region(block.terminator, open_, regions, fail)
    return open_


def _regions_of_views(function: SlirFunction, views: set[LocalId],
                      fail: Fail) -> dict[LocalId, frozenset[BorrowId]]:
    """The regions of each view local: the `BeginBorrow`s that name it (ir.md 8.6). A region
    has one view, and it is a view local. A view that no region names reads a value that no
    local owns, and the rule does not apply to it."""
    named: dict[BorrowId, Optional[LocalId]] = {}
    regions: dict[LocalId, set[BorrowId]] = {}
    for block in function.blocks:
        for stmt in block.statements:
            if not isinstance(stmt, st.BeginBorrow):
                continue
            if stmt.view is not None and stmt.view not in views:
                fail(VIEW_LOCAL, f"b{stmt.id.index} names _{stmt.view.index}, which is no "
                                 f"view local", stmt.span)
            if named.setdefault(stmt.id, stmt.view) != stmt.view:
                fail(VIEW_LOCAL, f"b{stmt.id.index} names two views", stmt.span)
            if stmt.view is not None:
                regions.setdefault(stmt.view, set()).add(stmt.id)
    return {view: frozenset(ids) for view, ids in regions.items()}


def _view_in_region(node: st.SlirStatement | tm.SlirTerminator, open_: _Open,
                    regions: dict[LocalId, frozenset[BorrowId]], fail: Fail) -> None:
    """At each read of a view local, each region over its owners is open. A `poke self` call
    with a `peek` return writes its answer before the region starts."""
    answer = node.dest if isinstance(node, st.Call) else None
    read = ({ident for operand in st.operands_of(node) for ident in _read_ids(operand)}
            if isinstance(node, st.Call) else set())
    for local in _uses(node):
        closed = regions.get(local, frozenset()) - open_.keys()
        if closed and (answer != p.Local(local) or local in read):
            fail(VIEW_LOCAL, f"_{local.index} is read while its regions {_names(closed)} "
                             f"are closed", _span(node))


def _read_ids(operand: p.Operand | p.Place) -> tuple[LocalId, ...]:
    place = operand.place if isinstance(operand, (p.Copy, p.Move)) else operand
    if isinstance(place, (p.Local, p.Global, p.Field, p.Index, p.Downcast, p.Deref)):
        return p.locals_read(place)
    return ()


def _address_in_region(node: st.SlirStatement | tm.SlirTerminator, open_: _Open,
                       refs: dict[LocalId, frozenset[p.Place]], fail: Fail) -> None:
    """At each use of an address local, a region over the root of the place of each of its
    `Ref`s is open."""
    if not refs:
        return
    held = _held(open_)
    for local in _uses(node):
        for root in sorted(refs.get(local, frozenset()) - held, key=show):
            fail(ADDRESS_LOCAL, f"_{local.index} is used while no region over "
                                f"{show(root)} is open", _span(node))


def _held(open_: _Open) -> set[p.Place]:
    """The owners of the open regions. Two paths that meet can bring one region over two
    owners (the parts of a tuple literal, ir.md 8.9)."""
    return {owner for owners in open_.values() for owner in owners}


def _written_before_use(function: SlirFunction, addresses: frozenset[LocalId],
                        fail: Fail) -> None:
    """The `Ref` of each address local comes before each use of it, on every path: a block
    knows the address locals that each path into it wrote (the meet is the intersection)."""
    blocks = {block.id: block for block in function.blocks}
    at_top: dict[BlockId, frozenset[LocalId]] = {function.entry: frozenset()}
    work = [function.entry]
    while work:
        block = blocks[work.pop()]
        written = set(at_top[block.id])
        for stmt in [*block.statements, block.terminator]:
            for local in _uses(stmt):
                if local in addresses and local not in written:
                    fail(ADDRESS_LOCAL, f"_{local.index} is used before its Ref on a path",
                         _span(stmt))
            if isinstance(stmt, st.Assign) and isinstance(stmt.rvalue, r.Ref) \
                    and isinstance(stmt.place, p.Local):
                written.add(stmt.place.id)
        for target in successors(block.terminator):
            known = at_top.get(target)
            meet = frozenset(written) if known is None else known & written
            if meet != known:
                at_top[target] = meet
                work.append(target)


def _one_write_on_each_path(function: SlirFunction, views: frozenset[LocalId],
                            addresses: frozenset[LocalId], fail: Fail) -> None:
    """ONE statement writes a view local on each path, and one `Ref` an address local: a
    block knows the statements that each path into it wrote each local with (the meet is
    the union), and the storage markers of the local forget them. Two paths that each wrote
    the local one time can meet (the binders of an alternative, ir.md 8.9), and a later
    round of a loop writes it again with the same statement (the step of a walk)."""
    blocks = {block.id: block for block in function.blocks}
    at_top: dict[BlockId, dict[LocalId, frozenset[tuple[int, int]]]] = {function.entry: {}}
    work = [function.entry]
    while work:
        block = blocks[work.pop()]
        writers = dict(at_top[block.id])
        for at, stmt in enumerate(block.statements):
            if isinstance(stmt, (st.StorageLive, st.StorageDead)):
                writers.pop(stmt.local, None)
                continue
            target = _written(stmt)
            if target is None or not (target.id in views or (
                    target.id in addresses and isinstance(stmt, st.Assign)
                    and isinstance(stmt.rvalue, r.Ref))):
                continue
            here = (block.id.index, at)
            if writers.get(target.id, frozenset()) - {here}:
                rule, what = ((VIEW_LOCAL, "written") if target.id in views
                              else (ADDRESS_LOCAL, "written by a Ref"))
                fail(rule, f"{show(target)} is {what} two times on a path", stmt.span)
            writers[target.id] = frozenset({here})
        for target_id in successors(block.terminator):
            known = at_top.get(target_id)
            meet = writers if known is None else {
                local: known.get(local, frozenset()) | writers.get(local, frozenset())
                for local in known.keys() | writers.keys()}
            if meet != known:
                at_top[target_id] = meet
                work.append(target_id)


def _uses(node: st.SlirStatement | tm.SlirTerminator) -> Iterator[LocalId]:
    """The locals that `node` reads or writes as a value. The write of a `Ref` and the
    storage and region markers are no use."""
    parts: list[p.Operand | p.Place] = []
    if isinstance(node, (st.Assign, st.Call)):
        parts += st.operands_of(node)
    if isinstance(node, st.Assign) and not isinstance(node.rvalue, r.Ref):
        parts.append(node.place)
    elif isinstance(node, st.Call):
        parts.append(node.dest)
    elif isinstance(node, (st.Drop, st.DropIfSet)):
        parts.append(node.place)
    elif isinstance(node, tm.SwitchInt):
        parts.append(node.operand)
    elif isinstance(node, tm.Trap):
        parts += list(node.args)
    for part in parts:
        place = part.place if isinstance(part, (p.Copy, p.Move)) else part
        if isinstance(place, (p.Local, p.Global, p.Field, p.Index, p.Downcast, p.Deref)):
            yield from p.locals_read(place)


def _span(node: st.SlirStatement | tm.SlirTerminator) -> Optional[Span]:
    return getattr(node, "span", None)


def _owners_kept(stmt: st.SlirStatement, open_: _Open, fail: Fail) -> None:
    """No write, move, drop or `Inout` argument of an owner of an open region."""
    owners = _held(open_)
    if not owners:
        return
    for place, what in changes(stmt):
        if p.root_of(place) in owners:
            fail(REGION_OWNER, f"{what} {show(place)} while a region over it is open",
                 stmt.span)


def changes(stmt: st.SlirStatement) -> Iterator[tuple[p.Place, str]]:
    """Each place that `stmt` changes, and how: the one rule of a change of an owner, which
    drop placement reads too (`DropPlacement.end_before_change`)."""
    yield from _drops_and_moves(stmt)
    target = _written_place(stmt)
    if target is not None:
        yield target, "writes"
    if isinstance(stmt, st.Call):
        yield from ((operand.place, "gives to an inout argument")
                    for operand, convention in stmt.args
                    if isinstance(operand, p.Copy) and convention is Convention.INOUT)


def _copied(value: r.Rvalue, slot: bool) -> bool:
    """A view local is written by a `Copy`; the `_0` of a `peek` return also by an aggregate
    (ir.md 8.13)."""
    if isinstance(value, r.Use):
        return isinstance(value.operand, p.Copy)
    return slot and isinstance(value, r.Aggregate)


def _view_locals(function: SlirFunction, views: set[LocalId], fail: Fail) -> None:
    for block in function.blocks:
        for stmt in block.statements:
            target = _written(stmt)
            if target is not None and target.id in views:
                if isinstance(stmt, st.Assign) and not _copied(stmt.rvalue, target.id.index == 0):
                    fail(VIEW_LOCAL, f"{show(target)} is written by no copy", stmt.span)
            for place, what in _spends(stmt):
                local = p.root_of(place)
                if isinstance(local, p.Local) and local.id in views:
                    fail(VIEW_LOCAL, f"{what} the view {show(place)}, which owns nothing",
                         stmt.span)


def _address_locals(function: SlirFunction, addresses: frozenset[LocalId],
                    fail: Fail) -> dict[LocalId, frozenset[p.Place]]:
    """A `Ref` writes the address local itself, not a part of it (`_one_write_on_each_path`
    counts the `Ref`s on each path). The local is never moved, at its root or at a part, and
    never dropped as a whole but at a rebind through it, where the next statement writes it:
    a write of an owning part drops the old part through the address (rule 6). Answers the
    roots of the places of the `Ref`s of each address local."""
    refs: dict[LocalId, frozenset[p.Place]] = {}
    for block in function.blocks:
        for at, stmt in enumerate(block.statements):
            if isinstance(stmt, st.Assign) and isinstance(stmt.rvalue, r.Ref):
                if not isinstance(stmt.place, p.Local):
                    fail(ADDRESS_LOCAL, f"a Ref writes {show(stmt.place)}, which is no local",
                         stmt.span)
                refs[stmt.place.id] = refs.get(stmt.place.id, frozenset()) | {
                    p.root_of(stmt.rvalue.place)}
            following = block.statements[at + 1:at + 2]
            for place, what in _spends(stmt):
                local = p.root_of(place)
                whole = what == "drops" and isinstance(place, p.Local) and not (
                    isinstance(stmt, st.Drop) and _writes(following, place))
                if isinstance(local, p.Local) and local.id in addresses and (
                        whole or what == "moves"):
                    fail(ADDRESS_LOCAL, f"{what} the address local {show(place)}, which owns "
                                        f"nothing", stmt.span)
    return refs


def _writes(statements: Sequence[st.SlirStatement], place: p.Local) -> bool:
    """Whether the one statement of `statements` writes `place` with no `Ref`."""
    return any(isinstance(stmt, st.Assign) and stmt.place == place
               and not isinstance(stmt.rvalue, r.Ref) for stmt in statements)


def _written_place(stmt: st.SlirStatement) -> Optional[p.Place]:
    return (stmt.place if isinstance(stmt, st.Assign)
            else stmt.dest if isinstance(stmt, st.Call) else None)


def _written(stmt: st.SlirStatement) -> Optional[p.Local]:
    place = _written_place(stmt)
    return place if isinstance(place, p.Local) else None


def _drops_and_moves(stmt: st.SlirStatement) -> Iterator[tuple[p.Place, str]]:
    if isinstance(stmt, (st.Drop, st.DropIfSet)):
        yield stmt.place, "drops"
    yield from ((part.place, "moves") for part in st.operands_of(stmt)
                if isinstance(part, p.Move))


def _spends(stmt: st.SlirStatement) -> Iterator[tuple[p.Place, str]]:
    yield from _drops_and_moves(stmt)
    if isinstance(stmt, st.Call):
        yield from ((operand.place, "gives to an owned argument")
                    for operand, convention in stmt.args
                    if isinstance(operand, p.Copy) and convention is Convention.OWNED)


def _names(open_: _Open | frozenset[BorrowId]) -> str:
    return "{" + ", ".join(f"b{ident.index}" for ident in sorted(open_)) + "}"
