"""Path-sensitive facts and the branch / loop joins that carry them."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.ast import Block, Break, Continue, If, Match, Return

from .state import BorrowState

if TYPE_CHECKING:
    from . import BorrowChecker


@dataclass(frozen=True)
class FlowFacts:
    """The per-variable facts that must survive a branch join or a loop back edge.

    Which join rule a field takes is the whole design. `moved`, `destroyed` and
    `invalidation` are monotone and join by UNION, so a loop converges in two passes.
    `owns_no_heap` GRANTS permission, so it joins by INTERSECTION -- believable after the
    join only if it held on every path; union would be unsound. Because intersection has no
    empty identity, paths go through `join()` over the surviving list, never a fold into a
    blank `FlowFacts()`.
    """
    moved: frozenset[str] = frozenset()
    destroyed: frozenset[str] = frozenset()
    owns_no_heap: frozenset[str] = frozenset()
    # A tuple, not a frozenset: `Span` is an unfrozen dataclass and so unhashable. The
    # span must travel with the flag, or CE2412 renders with no location.
    invalidation: tuple = ()

    def __or__(self, other: "FlowFacts") -> "FlowFacts":
        """Join two paths. Union for the monotone facts, intersection for permission."""
        seen = {name for name, _span, _by in self.invalidation}
        merged = self.invalidation + tuple(
            entry for entry in other.invalidation if entry[0] not in seen
        )
        return FlowFacts(
            moved=self.moved | other.moved,
            destroyed=self.destroyed | other.destroyed,
            owns_no_heap=self.owns_no_heap & other.owns_no_heap,
            invalidation=merged,
        )

    def without(self, names: frozenset[str]) -> "FlowFacts":
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
            invalidation=tuple(e for e in self.invalidation if e[0] not in names),
        )

    def invalidation_of(self, name: str) -> Optional[tuple]:
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


def reinitialize(state: BorrowState) -> None:
    """A rebind RE-INITIALIZES the binding: every fact about the OLD VALUE is stale.

    Only the value facts. `is_borrowed_binding`, `is_let_borrow` and `borrows_from` say
    WHERE the storage is, and no rebind moves storage -- clearing them let one rebind
    launder a binding into a writable local, so a field write that was CE2426 on the line
    above passed on the line below (#590).
    """
    state.is_moved = False
    state.moved_at_span = None
    state.move_reported_by = None
    state.is_destroyed = False
    state.invalidated_at = None
    state.invalidated_by = ()


def terminates(node, *, leaves_round: bool = False) -> bool:
    """Does every path through this statement (or block) leave the function?

    With `leaves_round`, a `break` and a `continue` end a path too: they leave the round
    of the loop, and the loop frame keeps their facts (#993). This is the question of a
    join inside a loop body. A nested loop is not descended: a `break` in it ends that
    loop's round, not this path.
    """
    match node:
        case Return():
            return True
        case Break() | Continue():
            return leaves_round
        case Block():
            # Any terminating statement terminates the block. Later statements are
            # unreachable; they are still checked, which over-checks and never
            # under-checks.
            return any(terminates(stmt, leaves_round=leaves_round)
                       for stmt in node.statements)
        case If():
            return bool(node.else_block) and (
                all(terminates(arm, leaves_round=leaves_round) for _cond, arm in node.arms)
                and terminates(node.else_block, leaves_round=leaves_round))
        case Match():
            arms = getattr(node, "arms", ())
            return bool(arms) and all(terminates(arm.body, leaves_round=leaves_round)
                                      for arm in arms)
        case _:
            return False


@dataclass
class LoopFrame:
    """The paths that leave one round of a loop body early (#993).

    A `continue` path goes to the back edge; a `break` path goes to the code after the
    loop and never to the next round.
    """
    breaks: list[FlowFacts] = field(default_factory=list)
    continues: list[FlowFacts] = field(default_factory=list)


@dataclass(frozen=True)
class LoopFlow:
    """The facts at the four points of a loop that the borrow pass reads (#993).

    `fixed_point` is where the second, reporting round starts (`entry` joined with the
    back edge of the first round), so a fact in it and not in `entry` came round the back
    edge. `back_edge` joins every path that reaches the next round, of both rounds: a
    view that lives for the whole loop reads its invalidation here. `exit` is what the
    code after the loop sees: the loop head's facts joined with every `break` path.
    """
    entry: FlowFacts
    fixed_point: FlowFacts
    back_edge: FlowFacts
    exit: FlowFacts


def snapshot_flow(checker: 'BorrowChecker') -> FlowFacts:
    """Every path-sensitive fact (for branch and loop control-flow joins)."""
    states = checker.borrow_state.items()
    return FlowFacts(
        moved=frozenset(n for n, s in states if s.is_moved),
        destroyed=frozenset(n for n, s in states if s.is_destroyed),
        owns_no_heap=frozenset(n for n, s in states if s.owns_no_heap),
        invalidation=tuple((n, s.invalidated_at, s.invalidated_by)
                           for n, s in states if s.invalidated_at is not None),
    )


def restore_flow(checker: 'BorrowChecker', facts: FlowFacts) -> None:
    """Set every path-sensitive flag to exactly what `facts` says."""
    invalidation = {name: (span, by) for name, span, by in facts.invalidation}
    for name, state in checker.borrow_state.items():
        state.is_moved = name in facts.moved
        state.is_destroyed = name in facts.destroyed
        state.owns_no_heap = name in facts.owns_no_heap
        span, by = invalidation.get(name, (None, ()))
        state.invalidated_at = span
        state.invalidated_by = by
