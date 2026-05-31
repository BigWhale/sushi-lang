# semantics/passes/types/control_flow.py
"""
Return-reachability analysis for type validation (Pass 2).

Determines whether a block or statement returns on all code paths. This
powers the CE0107 ("missing return") check for functions, extension
methods, and perk implementation methods.

The analysis is effectively pure: it inspects AST node shapes only and does
not mutate the validator. Functions take the TypeValidator instance
(``self``) for consistency with the package's delegation pattern.
"""
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
    from sushi_lang.semantics.ast import Break, Continue, ExprStmt, Let, Rebind, Print, PrintLn, Foreach, While

    # Return statements always return
    if isinstance(stmt, Return):
        return True

    # If statements return if all branches return
    if isinstance(stmt, If):
        # Check if all arms return
        all_arms_return = all(block_always_returns(self, block) for _, block in stmt.arms)
        # If statement returns only if all arms return AND there's an else block that returns
        if stmt.else_block:
            return all_arms_return and block_always_returns(self, stmt.else_block)
        return False  # No else block means some paths don't return

    # Match statements return if all arms return
    if isinstance(stmt, Match):
        return all(
            block_always_returns(self, arm.body) if isinstance(arm.body, Block) else False
            for arm in stmt.arms
        )

    # Loops never guarantee a return (they might not execute or might break)
    if isinstance(stmt, (While, Foreach)):
        return False

    # Other statements don't return
    if isinstance(stmt, (Let, Rebind, ExprStmt, Print, PrintLn, Break, Continue)):
        return False

    # Unknown statement type - conservatively return False
    return False
