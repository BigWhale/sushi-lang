"""Return-reachability analysis for type validation (the typecheck pass)."""
from __future__ import annotations

from typing import Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import Block, Break, Continue, Lambda, Stmt, Return, If, Match


def block_always_returns(self, block: Block) -> bool:
    """Check if a block always returns on all code paths."""
    for stmt in block.statements:
        if statement_always_returns(self, stmt):
            return True
    return False


def statement_always_returns(self, stmt: Stmt) -> bool:
    """Check if a statement always returns on all code paths."""
    from sushi_lang.semantics.ast import Foreach, While

    if isinstance(stmt, Return):
        return True

    if isinstance(stmt, If):
        all_arms_return = all(block_always_returns(self, block) for _, block in stmt.arms)
        if stmt.else_block:
            return all_arms_return and block_always_returns(self, stmt.else_block)
        return False  # No else block means some paths don't return

    if isinstance(stmt, Match):
        return all(
            block_always_returns(self, arm.body) if isinstance(arm.body, Block) else False
            for arm in stmt.arms
        )

    # Loops never guarantee a return (they might not execute or might break)
    if isinstance(stmt, (While, Foreach)):
        return False

    # Everything else -- a let, a rebind, an expression statement, a print, a break, a
    # continue -- does not return either, so the answer is the same.
    return False


def ends_the_path(self, stmt: Stmt) -> bool:
    """Whether no statement after `stmt` in its block can run: it returns on every path,
    or it is a `break` / `continue`."""
    return isinstance(stmt, (Break, Continue)) or statement_always_returns(self, stmt)


def first_dead_statement(self, block: Block) -> Optional[tuple[Stmt, Stmt]]:
    """The statement that ends the path in `block` and the first one after it, if any."""
    statements = block.statements
    for ender, dead in zip(statements, statements[1:], strict=False):
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
