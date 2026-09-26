"""Return-reachability analysis for type validation (the typecheck pass)."""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import Block, Break, Continue, Lambda, Stmt, Return, If, Match


class Reach(Enum):
    """Where the path through a statement or a block goes."""
    ENDS = "ends"            # it returns on every path
    FALLS = "falls"          # some path reaches the statement after it
    UNDECIDED = "undecided"  # a `match` refused as not exhaustive decides it (#886)


def _branches(reaches: Iterable[Reach]) -> Reach:
    """The reach of a statement whose paths are these branches."""
    found = set(reaches)
    if Reach.FALLS in found:
        return Reach.FALLS
    if Reach.UNDECIDED in found:
        return Reach.UNDECIDED
    return Reach.ENDS


def block_reach(self, block: Block) -> Reach:
    undecided = False
    for stmt in block.statements:
        reach = statement_reach(self, stmt)
        if reach is Reach.ENDS:
            return Reach.ENDS
        undecided = undecided or reach is Reach.UNDECIDED
    return Reach.UNDECIDED if undecided else Reach.FALLS


def statement_reach(self, stmt: Stmt) -> Reach:
    if isinstance(stmt, Return):
        return Reach.ENDS

    if isinstance(stmt, If):
        if stmt.else_block is None:
            return Reach.FALLS
        blocks = [block for _, block in stmt.arms] + [stmt.else_block]
        return _branches(block_reach(self, block) for block in blocks)

    if isinstance(stmt, Match):
        reach = _branches(
            block_reach(self, arm.body) if isinstance(arm.body, Block) else Reach.FALLS
            for arm in stmt.arms
        )
        # A value that no arm covers goes past the `match`, but the missing arm is
        # already an error, and the arm that the author adds decides the path.
        if stmt.not_exhaustive and reach is Reach.ENDS:
            return Reach.UNDECIDED
        return reach

    # A loop may not run, or may break. Every other statement goes on to the next.
    return Reach.FALLS


def block_always_returns(self, block: Block) -> bool:
    """The fall-off rule's question (CE0107): no path certainly reaches the block end."""
    return block_reach(self, block) is not Reach.FALLS


def statement_always_returns(self, stmt: Stmt) -> bool:
    """The statement returns on every path, so no statement after it can run."""
    return statement_reach(self, stmt) is Reach.ENDS


def ends_the_path(self, stmt: Stmt) -> bool:
    """Whether no statement after `stmt` in its block can run: it returns on every path,
    or it is a `break` / `continue`."""
    return isinstance(stmt, (Break, Continue)) or statement_always_returns(self, stmt)


def first_dead_statement(self, block: Block) -> Optional[tuple[Stmt, Stmt]]:
    """The statement that ends the path in `block` and the first one after it, if any."""
    statements = block.statements
    for ender, dead in zip(statements, statements[1:], strict=False):
        # Two statements from different `expand` copies, or a copy and its
        # surroundings, were not written in sequence (#854).
        if ender.expand_copies != dead.expand_copies:
            continue
        if ends_the_path(self, ender):
            return ender, dead
    return None


def reject_dead_statements(self, body: Block) -> None:
    """CE0140, once per block of one body (#854).

    A lambda's body is its own callable: the lift pass types it as a function, so the
    walk does not enter it here.
    """
    from sushi_lang.semantics.ast_walk import walk_nodes

    def visit(node) -> bool:
        if isinstance(node, Lambda):
            return False
        if isinstance(node, Block):
            found = first_dead_statement(self, node)
            if found is not None:
                ender, dead = found
                self.err.emit_with(er.ERR.CE0140, dead.loc) \
                    .note_at("the path ends here", ender.loc) \
                    .help("remove the statement, or move it before the one that ends "
                          "the path") \
                    .emit()
        return True

    walk_nodes(body, visit)
