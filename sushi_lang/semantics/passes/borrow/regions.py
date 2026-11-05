"""The borrow region of a view (docs/design/ir.md 7.9, 8.6): the one writer of the region row.

A `let` that borrows (a `REJECT` row at its value) makes a view. `record_owners` gives the
owner set of the view at the `let`. When the walk of the body ends with no error,
`write_regions` writes one row for each view: the owner set, and the statements of the LAST
uses of the view. A last use is a statement that uses the view, after which no path uses it
again. The region ends there, and not at the end of the scope of the view, because a change
of an owner after the last use is legal.

The statements are read backwards from the end of the block of the view. A use in a loop
that does not hold the `let` is a use of the whole loop: the next round can use the view
again. A use in an arm of an `if` or a `match` gives the last uses of that arm, and an arm
with no use ends the region at its top (SLIR lowering). A view that no statement uses ends
its region at its `let`. A bare binder of an owning payload makes a view too: its uses
are read in its arm, and a binder that no statement uses has no last use.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Iterable

from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.ownership import Region
from sushi_lang.semantics.shir.walk import walk

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId, ShirId
    from sushi_lang.semantics.shir.ownership import OwnershipResults

    from . import BorrowChecker
    from .state import BorrowState, Owner


def note_view(checker: 'BorrowChecker', node: s.Assign, state: 'BorrowState') -> None:
    """Keep the view that the `let` `node` makes (a `REJECT` row at its value), with its
    owners and its statement. A discovery round of a loop writes no row, and keeps nothing."""
    if (checker.table().ownership_of(node.value.shir_id) is not Ownership.REJECT
            or state.local is None or not checker.statement_at):
        return
    block, index = checker.statement_at[-1]
    checker.views.setdefault(state.local, (state.borrows_from, block, index))


def note_part_view(checker: 'BorrowChecker', state: 'BorrowState') -> None:
    """Keep the view that a binder of a destructure of a borrowed value makes: its last
    uses are read after the destructure, the statement that the walk is in."""
    if state.local is None or not checker.statement_at:
        return
    block, index = checker.statement_at[-1]
    checker.views.setdefault(state.local, (state.borrows_from, block, index))


def note_binder(checker: 'BorrowChecker', state: 'BorrowState', body: ShirBlock) -> None:
    """Keep the view that a bare binder of an owning payload makes, with its owners and the
    statements of its arm `body`."""
    if state.local is not None:
        checker.views.setdefault(state.local, (state.borrows_from, body, -1))


def write_regions(views: dict[LocalId, tuple[tuple[Owner, ...], ShirBlock, int]],
                  table: OwnershipResults) -> None:
    """One region row for each view of the body, in `table`."""
    for view, (owners, block, index) in views.items():
        table.record_region(view, Region(owners, last_uses(block, index, view)))


def last_uses(block: ShirBlock, index: int, view: LocalId) -> tuple[ShirId, ...]:
    """The statements of the last uses of `view`, which the statement `index` of `block`
    declares, or which a binder of the arm `block` declares (`index` -1): a binder that no
    statement uses has none."""
    anchors, _ = _scan(block.statements[index + 1:], view)
    if anchors or index < 0:
        return tuple(sorted(anchors))
    return (block.statements[index].shir_id,)


def _scan(statements: Iterable[s.ShirStmt], view: LocalId) -> tuple[set[ShirId], bool]:
    """The last uses of `view` in `statements`, and whether one of them uses it."""
    anchors: set[ShirId] = set()
    used = False
    for stmt in reversed(list(statements)):
        if not _uses(stmt, view):
            continue
        if not used:
            anchors |= _last_in(stmt, view)
        used = True
    return anchors, used


def _last_in(stmt: s.ShirStmt, view: LocalId) -> set[ShirId]:
    """The last uses in a statement that no later statement follows with a use: the last
    uses of each arm of a branch, else the statement itself."""
    arms: list[ShirBlock] = []
    if isinstance(stmt, s.If):
        arms = [stmt.then] + ([stmt.otherwise] if stmt.otherwise is not None else [])
    elif isinstance(stmt, s.Match):
        arms = [arm.body for arm in stmt.arms]
    found: set[ShirId] = set()
    for arm in arms:
        found |= _scan(arm.statements, view)[0]
    return found or {stmt.shir_id}


def _uses(stmt: s.ShirStmt, view: LocalId) -> bool:
    return any(isinstance(value, e.LocalRef) and value.local == view
               for _path, value in walk(stmt, ""))
