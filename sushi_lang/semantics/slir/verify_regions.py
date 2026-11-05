"""The region rules and the view-local rules of the SLIR verifier (docs/design/ir.md 8.6, 8.13).

A view local (8.2) is written by one statement: a `Copy` of a place, or a call whose answer
is a view. It is never the place of a `Drop` or a `DropIfSet`, never `Move`d and never the
operand of an `Owned` argument, at its root or at a projection of it. One `Ref` writes an
address local (8.2), and it is never dropped as a whole and never moved.

A borrow region is a `BeginBorrow` of an owner and the `EndBorrow` of its id. A dataflow over
the CFG gives each block the set of open regions at its top: every path into a block brings
the same set, a region begins one time and ends one time on a path, and no region is open
at a `return`. A `Trap` ends the program, so it closes nothing. While a region over an owner
is open, no statement writes that owner, moves it, drops it or gives it to an `Inout`
argument, at its root or at a projection of it. A change of the owner after the `EndBorrow`
is legal.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterator, NoReturn, Optional

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
    _Open: TypeAlias = dict[BorrowId, p.Place]


def check_regions(function: SlirFunction, fail: Fail) -> None:
    """CE0158, through `fail`, at the first view-local rule or region rule that `function`
    breaks."""
    views = {local.id for local in function.locals if local.view}
    if views:
        _view_locals(function, views, fail)
    addresses = address_locals(function)
    if addresses:
        _address_locals(function, addresses, fail)
    blocks = {block.id: block for block in function.blocks}
    at_top: dict[BlockId, _Open] = {function.entry: {}}
    work = [function.entry]
    while work:
        block = blocks[work.pop()]
        open_ = _through(block, dict(at_top[block.id]), fail)
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


def _through(block: SlirBlock, open_: _Open, fail: Fail) -> _Open:
    for stmt in block.statements:
        if isinstance(stmt, st.BeginBorrow):
            if stmt.id in open_:
                fail(REGION, f"the region b{stmt.id.index} begins again while it is open",
                     stmt.span)
            open_[stmt.id] = p.root_of(stmt.place)
        elif isinstance(stmt, st.EndBorrow):
            if open_.pop(stmt.id, None) is None:
                fail(REGION, f"the region b{stmt.id.index} ends, and it is not open", stmt.span)
        else:
            _owners_kept(stmt, open_, fail)
    return open_


def _owners_kept(stmt: st.SlirStatement, open_: _Open, fail: Fail) -> None:
    """No write, move, drop or `Inout` argument of an owner of an open region."""
    owners = set(open_.values())
    if not owners:
        return
    for place, what in changes(stmt):
        if p.root_of(place) in owners:
            fail(REGION_OWNER, f"{what} {show(place)} while a region over it is open",
                 stmt.span)


def changes(stmt: st.SlirStatement) -> Iterator[tuple[p.Place, str]]:
    """Each place that `stmt` changes, and how: the one rule of a change of an owner, which
    drop placement reads too (`DropPlacement.end_before_change`)."""
    if isinstance(stmt, (st.Drop, st.DropIfSet)):
        yield stmt.place, "drops"
    if isinstance(stmt, st.Assign):
        yield stmt.place, "writes"
        yield from ((part.place, "moves") for part in r.operands_of(stmt.rvalue)
                    if isinstance(part, p.Move))
    if isinstance(stmt, st.Call):
        yield stmt.dest, "writes"
        for operand, convention in stmt.args:
            if isinstance(operand, p.Move):
                yield operand.place, "moves"
            elif isinstance(operand, p.Copy) and convention is Convention.INOUT:
                yield operand.place, "gives to an inout argument"


def _view_locals(function: SlirFunction, views: set[LocalId], fail: Fail) -> None:
    written: set[LocalId] = set()
    for block in function.blocks:
        for stmt in block.statements:
            target = _written(stmt)
            if target is not None and target.id in views:
                if target.id in written:
                    fail(VIEW_LOCAL, f"{show(target)} is written two times", stmt.span)
                written.add(target.id)
                if isinstance(stmt, st.Assign) and not (
                        isinstance(stmt.rvalue, r.Use) and isinstance(stmt.rvalue.operand, p.Copy)):
                    fail(VIEW_LOCAL, f"{show(target)} is written by no copy", stmt.span)
            for place, what in _spends(stmt):
                local = p.root_of(place)
                if isinstance(local, p.Local) and local.id in views:
                    fail(VIEW_LOCAL, f"{what} the view {show(place)}, which owns nothing",
                         stmt.span)


def _address_locals(function: SlirFunction, addresses: frozenset[LocalId],
                    fail: Fail) -> None:
    """One `Ref` writes each address local, and it writes the local itself, not a part of
    it. The local is never dropped as a whole and never moved, at its root or at a part:
    a write of an owning part drops the old part through the address (rule 6)."""
    refs: set[LocalId] = set()
    for block in function.blocks:
        for stmt in block.statements:
            if isinstance(stmt, st.Assign) and isinstance(stmt.rvalue, r.Ref):
                if not isinstance(stmt.place, p.Local):
                    fail(ADDRESS_LOCAL, f"a Ref writes {show(stmt.place)}, which is no local",
                         stmt.span)
                if stmt.place.id in refs:
                    fail(ADDRESS_LOCAL, f"{show(stmt.place)} is written by two Refs",
                         stmt.span)
                refs.add(stmt.place.id)
            for place, what in _spends(stmt):
                local = p.root_of(place)
                whole = what == "drops" and isinstance(place, p.Local)
                if isinstance(local, p.Local) and local.id in addresses and (
                        whole or what == "moves"):
                    fail(ADDRESS_LOCAL, f"{what} the address local {show(place)}, which owns "
                                        f"nothing", stmt.span)


def _written(stmt: st.SlirStatement) -> Optional[p.Local]:
    place = (stmt.place if isinstance(stmt, st.Assign)
             else stmt.dest if isinstance(stmt, st.Call) else None)
    return place if isinstance(place, p.Local) else None


def _spends(stmt: st.SlirStatement) -> Iterator[tuple[p.Place, str]]:
    if isinstance(stmt, (st.Drop, st.DropIfSet)):
        yield stmt.place, "drops"
    operands = (r.operands_of(stmt.rvalue) if isinstance(stmt, st.Assign)
                else tuple(operand for operand, _ in stmt.args) if isinstance(stmt, st.Call)
                else ())
    yield from ((part.place, "moves") for part in operands if isinstance(part, p.Move))
    if isinstance(stmt, st.Call):
        yield from ((operand.place, "gives to an owned argument")
                    for operand, convention in stmt.args
                    if isinstance(operand, p.Copy) and convention is Convention.OWNED)


def _names(open_: _Open) -> str:
    return "{" + ", ".join(f"b{ident.index}" for ident in sorted(open_)) + "}"
