"""Path-sensitive facts and the branch / loop joins that carry them."""

from __future__ import annotations
from dataclasses import dataclass

from sushi_lang.semantics.ast import Block, If, Match, Return

from .state import BorrowState


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
    """A rebind RE-INITIALIZES the binding: every fact about the OLD value is stale."""
    state.is_moved = False
    state.moved_at_span = None
    state.is_destroyed = False
    state.invalidated_at = None
    state.invalidated_by = ()
    state.is_borrowed_binding = False
    state.is_let_borrow = False
    state.borrows_from = None


def terminates(node) -> bool:
    """Does every path through this statement (or block) leave the function?"""
    if isinstance(node, Return):
        return True
    if isinstance(node, Block):
        # Any terminating statement terminates the block. Later statements are
        # unreachable; they are still checked, which over-checks and never under-checks.
        return any(terminates(stmt) for stmt in node.statements)
    if isinstance(node, If):
        if not node.else_block:
            return False
        return (all(terminates(arm) for _cond, arm in node.arms)
                and terminates(node.else_block))
    if isinstance(node, Match):
        arms = getattr(node, "arms", ())
        return bool(arms) and all(terminates(arm.body) for arm in arms)
    return False
