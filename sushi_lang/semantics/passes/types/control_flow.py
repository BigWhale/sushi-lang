"""Return-reachability analysis for type validation (the typecheck pass)."""
from __future__ import annotations

from sushi_lang.semantics.ast import Block, Stmt, Return, If, Match


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
