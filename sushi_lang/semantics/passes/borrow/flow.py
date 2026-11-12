"""Path-sensitive facts and the branch / loop joins that carry them, keyed by `LocalId`.

The joins read the one path predicate of SHIR (`semantics/shir/paths.py`), which the
typecheck pass reads too. At each EXIT of a scope -- the end of a block that the path
reaches, a `return`, the error path of a `??`, a `break` or a `continue` -- the pass
records each local of the scope that is not moved on every path there
(`record_exit`). A rebind drops the old value of its local, so it is an exit of that value
(`record_rebind`). A local that is moved on some path and that an exit sees not moved on
every path is CONDITIONALLY moved: SLIR lowering gives it a drop flag (docs/design/ir.md
8.10, rule 4). A local that each exit sees moved on every path gets no drop (rule 3). A
by-value parameter that owns holds the value of the caller until a rebind, so for an exit
it is moved where no rebind came before (rule 5).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.shir.paths import terminates as terminates

from .state import BorrowState

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId

    from . import BorrowChecker


@dataclass(frozen=True)
class FlowFacts:
    """The per-variable facts that must survive a branch join or a loop back edge.

    Which join rule a field takes is the whole design. `moved`, `destroyed` and
    `invalidation` are monotone and join by UNION, so a loop converges in two passes.
    `owns_no_heap` GRANTS permission, so it joins by INTERSECTION -- believable after the
    join only if it held on every path; union would be unsound. `moved_on_every_path`
    joins by INTERSECTION too: a local in `moved` and not in it is moved on some paths
    only. Because intersection has no empty identity, paths go through `join()` over the
    surviving list, never a fold into a blank `FlowFacts()`.

    `moved_at` and `invalidation` hold one `(local, span, cause)` entry per local, so a join
    keeps the location with the flag. A path that rebinds the local has no entry, and the
    join keeps the entry of the path that moved it.
    """
    moved: frozenset[LocalId] = frozenset()
    destroyed: frozenset[LocalId] = frozenset()
    owns_no_heap: frozenset[LocalId] = frozenset()
    moved_on_every_path: frozenset[LocalId] = frozenset()
    caller_value: frozenset[LocalId] = frozenset()
    caller_value_on_every_path: frozenset[LocalId] = frozenset()
    # Tuples, not frozensets: `Span` is an unfrozen dataclass and so unhashable. The span
    # must travel with the flag, or CE2405, CE2435 and CE2412 render with no location.
    # A `moved_at` entry is (name, where it moved, the `nom self` method that took it).
    moved_at: tuple = ()
    invalidation: tuple = ()

    def __or__(self, other: "FlowFacts") -> "FlowFacts":
        """Join two paths. Union for the monotone facts, intersection for permission."""
        return FlowFacts(
            moved=self.moved | other.moved,
            destroyed=self.destroyed | other.destroyed,
            owns_no_heap=self.owns_no_heap & other.owns_no_heap,
            moved_on_every_path=self.moved_on_every_path & other.moved_on_every_path,
            caller_value=self.caller_value | other.caller_value,
            caller_value_on_every_path=(self.caller_value_on_every_path
                                        & other.caller_value_on_every_path),
            moved_at=_union_by_name(self.moved_at, other.moved_at),
            invalidation=_union_by_name(self.invalidation, other.invalidation),
        )

    def without(self, names: frozenset[LocalId]) -> "FlowFacts":
        """Drop every fact about `names`: bindings a loop RE-CREATES on each pass.

        A `next()` protocol item is declared once in the checker and once per iteration
        in the program, so a move of it inside the body must not travel the back edge --
        the next iteration holds a new value (#548).
        """
        if not names:
            return self
        return FlowFacts(
            moved=self.moved - names,
            destroyed=self.destroyed - names,
            owns_no_heap=self.owns_no_heap - names,
            moved_on_every_path=self.moved_on_every_path - names,
            caller_value=self.caller_value - names,
            caller_value_on_every_path=self.caller_value_on_every_path - names,
            moved_at=tuple(e for e in self.moved_at if e[0] not in names),
            invalidation=tuple(e for e in self.invalidation if e[0] not in names),
        )

    def invalidation_of(self, name: LocalId) -> Optional[tuple]:
        """The span and the cause of the change that invalidated `name`, or None."""
        for entry_name, span, by in self.invalidation:
            if entry_name == name:
                return span, by
        return None

    @staticmethod
    def join(paths: list["FlowFacts"]) -> "FlowFacts":
        """Join every surviving path of a branch."""
        if not paths:
            return FlowFacts()
        result = paths[0]
        for facts in paths[1:]:
            result = result | facts
        return result


def _union_by_name(first: tuple, second: tuple) -> tuple:
    """The entries of both paths, one per name; the first path's entry wins."""
    seen = {entry[0] for entry in first}
    return first + tuple(entry for entry in second if entry[0] not in seen)


def reinitialize(state: BorrowState) -> None:
    """A rebind RE-INITIALIZES the binding: every fact about the OLD VALUE is stale.

    Only the value facts. `is_borrowed_binding`, `is_let_borrow` and `borrows_from` say
    WHERE the storage is, and no rebind moves storage -- clearing them let one rebind
    launder a binding into a writable local, so a field write that was CE2426 on the line
    above passed on the line below (#590).
    """
    state.is_moved = False
    state.is_moved_on_every_path = False
    state.holds_caller_value = False
    state.holds_caller_value_on_every_path = False
    state.moved_at_span = None
    state.consumed_by_method = None
    state.move_reported_by = None
    state.is_destroyed = False
    state.invalidated_at = None
    state.invalidated_by = ()


@dataclass
class LoopFrame:
    """The paths that leave one round of a loop body early (#993).

    A `continue` path goes to the back edge; a `break` path goes to the code after the
    loop and never to the next round. `blocks` is the count of the blocks that are open
    outside the loop body: a `break` and a `continue` leave each block after them.
    """
    blocks: int
    breaks: list[FlowFacts] = field(default_factory=list)
    continues: list[FlowFacts] = field(default_factory=list)


def snapshot_flow(checker: 'BorrowChecker') -> FlowFacts:
    """Every path-sensitive fact (for branch and loop control-flow joins)."""
    states = checker.borrow_state.items()
    return FlowFacts(
        moved=frozenset(n for n, s in states if s.is_moved),
        destroyed=frozenset(n for n, s in states if s.is_destroyed),
        owns_no_heap=frozenset(n for n, s in states if s.owns_no_heap),
        moved_on_every_path=frozenset(n for n, s in states if s.is_moved_on_every_path),
        caller_value=frozenset(n for n, s in states if s.holds_caller_value),
        caller_value_on_every_path=frozenset(n for n, s in states
                                             if s.holds_caller_value_on_every_path),
        moved_at=tuple((n, s.moved_at_span, s.consumed_by_method)
                       for n, s in states if s.is_moved),
        invalidation=tuple((n, s.invalidated_at, s.invalidated_by)
                           for n, s in states if s.invalidated_at is not None),
    )


def restore_flow(checker: 'BorrowChecker', facts: FlowFacts) -> None:
    """Set every path-sensitive flag to exactly what `facts` says."""
    moved_at = {name: (span, method) for name, span, method in facts.moved_at}
    invalidation = {name: (span, by) for name, span, by in facts.invalidation}
    for name, state in checker.borrow_state.items():
        state.is_moved = name in facts.moved
        state.is_moved_on_every_path = name in facts.moved_on_every_path
        state.holds_caller_value = name in facts.caller_value
        state.holds_caller_value_on_every_path = name in facts.caller_value_on_every_path
        state.moved_at_span, state.consumed_by_method = moved_at.get(name, (None, None))
        state.is_destroyed = name in facts.destroyed
        state.owns_no_heap = name in facts.owns_no_heap
        span, by = invalidation.get(name, (None, ()))
        state.invalidated_at = span
        state.invalidated_by = by


def record_exit(checker: 'BorrowChecker', first_block: int = 0) -> None:
    """An exit of each block from `first_block` on: record each local of those blocks
    that is not moved on every path here.

    A discovery round of a loop records nothing: its facts are not the facts of the
    fixed point.
    """
    if checker.discovery_rounds:
        return
    for block in checker.block_locals[first_block:]:
        for local in block:
            state = checker.borrow_state.get(local)
            if state is not None and not state.is_moved_on_every_path:
                checker.kept_at_an_exit.add(local)
            if state is not None:
                _caller_value_on_some_paths(checker, state)


def record_rebind(checker: 'BorrowChecker', local: LocalId) -> None:
    """A rebind of `local` drops its old value (docs/design/ir.md 8.10, rule 6): record the
    local when the old value is moved on some paths and not on others, because only a drop
    flag tells the rebind whether to drop it."""
    if checker.discovery_rounds:
        return
    state = checker.borrow_state.get(local)
    if state is not None and state.is_moved and not state.is_moved_on_every_path:
        checker.kept_at_an_exit.add(local)
    if state is not None:
        _caller_value_on_some_paths(checker, state)


def _caller_value_on_some_paths(checker: 'BorrowChecker', state: BorrowState) -> None:
    """A by-value parameter that holds the value of the caller on some paths and a value
    of its own on others is conditionally moved: only a drop flag tells the exit or the
    rebind whether to drop it (docs/design/ir.md 8.10, rule 5)."""
    if state.holds_caller_value and not state.holds_caller_value_on_every_path \
            and state.local is not None:
        checker.moved_locals.add(state.local)
        checker.kept_at_an_exit.add(state.local)
