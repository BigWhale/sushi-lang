"""The ownership answers of the `borrow` pass for one body (docs/design/ir.md 7.9, ruling S6).

No SHIR node holds a fact that `borrow` finds. Each answer is a row of the
`OwnershipResults` of the body: one table for each `ShirBody`, one row kind for each
decision of the table in ir.md 7.9. Lowering lists each body with its table
(`LoweredProgram.ownership_table_of`), and the borrow pass is the one writer (gate:
`tests/unit/test_ownership_results_have_one_writer.py`). A key has one answer: a second
write is an internal error. When the pass ends, `ownership_check.py` finds each answer that
a node needs and that the table does not hold.

The rows and their keys:

- the `Ownership` of a consuming use (`semantics/ownership.py`: MOVE, ADOPT or REJECT), by
  the `shir_id` of the consumed node; a capture of a lambda by the `shir_id` of the
  `Lambda` and the captured local;
- the conditionally moved locals, by `LocalId`: SLIR lowering gives each one a drop flag
  (ir.md 8.10, rule 4);
- LENT or OWNED for an iterator producer or a lazy adapter, by the `shir_id` of the call;
- whether the state of a lent iterator goes to the heap, because the iterator leaves
  through a `peek` return, by the same key;
- whether a loop walks its iterator in place, by the `shir_id` of the `Loop`;
- the borrow region of a view, by the `LocalId` of the local that holds the view: its owner
  set, and the `shir_id` of the statement of its last use on each path (ir.md 8.6).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.side_table import SideTable, row_table, write_once

if TYPE_CHECKING:
    from typing import TypeAlias

    #: An owner of a view: a local, or the symbol of the global of a `var`.
    Owner: TypeAlias = LocalId | str


@dataclass(frozen=True, slots=True)
class Region:
    """The borrow region of a view: each owner that it freezes, the statement of its last use
    on each path, and its leaving ends (ir.md 7.9): the block of an arm that leaves the scope
    of the view with no use of it, and each last use in an arm that leaves."""
    owners: tuple[Owner, ...]
    until: tuple[ShirId, ...]
    leaving: frozenset[ShirId] = frozenset()


@dataclass(slots=True)
class OwnershipResults(SideTable):
    """The answers of `borrow` for one body, one row kind for each decision of ir.md 7.9."""
    _ownerships: dict[ShirId, Ownership] = row_table()
    _capture_ownerships: dict[tuple[ShirId, LocalId], Ownership] = row_table()
    _conditional_moves: dict[LocalId, bool] = row_table()
    _lent_iterators: dict[ShirId, bool] = row_table()
    _lent_on_heap: dict[ShirId, bool] = row_table()
    _in_place_walks: dict[ShirId, bool] = row_table()
    _regions: dict[LocalId, Region] = row_table()

    # -- the read API ----------------------------------------------------------------

    def ownership_of(self, use: ShirId) -> Optional[Ownership]:
        """The `Ownership` of the consuming use `use`, or None."""
        return self._ownerships.get(use)

    def capture_ownership_of(self, lam: ShirId, local: LocalId) -> Optional[Ownership]:
        """The `Ownership` of the capture of `local` by the lambda `lam`, or None."""
        return self._capture_ownerships.get((lam, local))

    def is_conditionally_moved(self, local: LocalId) -> bool:
        """Whether `local` is moved on some paths and not on others."""
        return local in self._conditional_moves

    def conditionally_moved(self) -> tuple[LocalId, ...]:
        """The conditionally moved locals of the body, in id order."""
        return tuple(sorted(self._conditional_moves))

    def lent_of(self, call: ShirId) -> Optional[bool]:
        """True for a LENT iterator, False for an OWNED one, None with no answer."""
        return self._lent_iterators.get(call)

    def lent_on_heap(self, call: ShirId) -> Optional[bool]:
        """Whether the state of a lent iterator goes to the heap, or None."""
        return self._lent_on_heap.get(call)

    def in_place_of(self, loop: ShirId) -> Optional[bool]:
        """Whether a loop walks its iterator in place, or None."""
        return self._in_place_walks.get(loop)

    def region_of(self, view: LocalId) -> Optional[Region]:
        """The borrow region of the view that `view` holds, or None."""
        return self._regions.get(view)

    def regions(self) -> tuple[tuple[LocalId, Region], ...]:
        """The borrow regions of the body, in the order of the view locals."""
        return tuple(sorted(self._regions.items(), key=lambda item: item[0]))

    # -- the write API: the borrow pass alone calls it ---------------------------------

    def record_ownership(self, use: ShirId, ownership: Ownership) -> None:
        write_once(self._ownerships, use, ownership, "borrow", "ownership")

    def record_capture_ownership(self, lam: ShirId, local: LocalId,
                                 ownership: Ownership) -> None:
        write_once(self._capture_ownerships, (lam, local), ownership, "borrow", "capture ownership")

    def record_conditional_move(self, local: LocalId) -> None:
        write_once(self._conditional_moves, local, True, "borrow", "conditional move")

    def record_lent(self, call: ShirId, lent: bool) -> None:
        write_once(self._lent_iterators, call, lent, "borrow", "iterator state")

    def record_lent_on_heap(self, call: ShirId, on_heap: bool) -> None:
        write_once(self._lent_on_heap, call, on_heap, "borrow", "heap decision")

    def record_in_place(self, loop: ShirId, in_place: bool) -> None:
        write_once(self._in_place_walks, loop, in_place, "borrow", "walk in place")

    def record_region(self, view: LocalId, region: Region) -> None:
        write_once(self._regions, view, region, "borrow", "borrow region")
