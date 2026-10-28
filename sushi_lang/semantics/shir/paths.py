"""Where the path through a SHIR statement goes (docs/design/ir.md 7.5).

ONE predicate for two passes: `typecheck` asks it whether a body can reach its end with no
`return` (CE0107) and whether a statement comes after one that ends the path (CE0140), and
`borrow` asks it at a join. `RULES` has a rule for each statement class (gate:
`tests/unit/test_shir_paths_are_total.py`).

A `return` leaves the function. With `leaves_round`, a `break` and a `continue` end a path
too: they leave the round of their loop, which is the question of a join in a loop body.
An `if` ends the path when it has an `else` and each of its blocks ends it. A `match` ends
it when it has an arm and each arm ends it, except a match of `uncovered`: its arms miss a
value (CE2040, CE2074), and the arm that the author adds decides its path (#886). A loop can
run zero times or break, so it never ends the path, and the predicate does not read its body.
Every other statement goes on to the next.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Collection, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import ShirBody
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.nodes import Hole, ShirBlock


@dataclass(frozen=True, slots=True)
class _Question:
    """What a rule asks: whether a jump ends a path, and the matches that do not end it."""
    leaves_round: bool
    uncovered: Collection[ShirId]


def terminates(node: s.ShirStmt | ShirBlock, *, leaves_round: bool = False,
               uncovered: Collection[ShirId] = frozenset()) -> bool:
    """Whether every path through `node`, a statement or a block, leaves the function, or
    with `leaves_round`, leaves the round of its loop. A match whose `shir_id` is in
    `uncovered` does not end the path."""
    return _ends(node, _Question(leaves_round, uncovered))


def _ends(node: s.ShirStmt | ShirBlock, question: _Question) -> bool:
    if isinstance(node, ShirBlock):
        return any(_ends(statement, question) for statement in node.statements)
    rule = RULES.get(type(node))
    if rule is None:
        er.raise_internal_error("CE0000", detail=f"the path predicate has no rule for the "
                                                 f"SHIR class '{type(node).__name__}'")
    return rule(node, question)


def ends_the_path(statement: s.ShirStmt, *,
                  uncovered: Collection[ShirId] = frozenset()) -> bool:
    """Whether no statement after `statement` in its block can run: it returns on every
    path, or it is a `break` or a `continue`."""
    return (isinstance(statement, (s.Break, s.Continue))
            or terminates(statement, uncovered=uncovered))


def first_dead_statement(block: ShirBlock, body: ShirBody, *,
                         uncovered: Collection[ShirId] = frozenset()
                         ) -> Optional[tuple[s.ShirStmt, s.ShirStmt]]:
    """The statement of `block`, a block of `body`, that ends the path and the first
    statement after it.

    Two statements from two copies of an unrolled `expand`, or a copy and a statement
    around it, were not written one after the other (#854), so the one cannot make the
    other dead.
    """
    statements, copies = block.statements, body.copies
    for index, (ender, dead) in enumerate(zip(statements, statements[1:], strict=False)):
        if copies.holding(block.shir_id, index) != copies.holding(block.shir_id, index + 1):
            continue
        if ends_the_path(ender, uncovered=uncovered):
            return ender, dead
    return None


def _returns(_node: s.Return, _question: _Question) -> bool:
    return True


def _jumps(_node: s.Break | s.Continue, question: _Question) -> bool:
    return question.leaves_round


def _if(node: s.If, question: _Question) -> bool:
    return node.otherwise is not None and all(
        _ends(block, question) for block in (node.then, node.otherwise))


def _match(node: s.Match, question: _Question) -> bool:
    return (bool(node.arms) and node.shir_id not in question.uncovered
            and all(_ends(arm.body, question) for arm in node.arms))


def _goes_on(_node: Any, _question: _Question) -> bool:
    return False


#: The rule of each statement class: whether every path through the statement leaves.
RULES: dict[type, Callable[[Any, _Question], bool]] = {
    s.Return: _returns,
    s.Break: _jumps,
    s.Continue: _jumps,
    s.If: _if,
    s.Match: _match,
    s.Assign: _goes_on,
    s.Destructure: _goes_on,
    s.ExprStmt: _goes_on,
    s.Loop: _goes_on,
    s.Assert: _goes_on,
    s.Expand: _goes_on,
    Hole: _goes_on,
}
