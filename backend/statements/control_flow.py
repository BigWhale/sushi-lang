"""
Control flow statement emission for the Sushi language compiler.

This module handles the generation of LLVM IR for control flow statements
including if/elif/else and while loops.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from internals.errors import raise_internal_error
from backend.utils import require_both_initialized

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from semantics.ast import If, While


def emit_if(codegen: 'LLVMCodegen', node: 'If') -> None:
    """Emit if statement with proper basic block structure.

    Handles multiple elif branches and optional else clause with proper
    phi node merging at the end.

    Args:
        codegen: The main LLVMCodegen instance.
        node: The if statement node to emit.
    """
    builder, func = require_both_initialized(codegen)
    codegen.utils.ensure_open_block()

    arms = list(node.arms)
    n = len(arms)

    after_bb = codegen.func.append_basic_block(name="if.end")
    body_bbs = [codegen.func.append_basic_block(name=f"if.{i}.body") for i in range(n)]
    else_bb = codegen.func.append_basic_block(name="if.else") if node.else_block is not None else None
    test_bbs = [codegen.func.append_basic_block(name=f"if.{i}.test") for i in range(1, n)]

    cond0 = codegen.utils.as_i1(codegen.expressions.emit_expr(arms[0][0], to_i1=True))
    false0 = test_bbs[0] if n > 1 else (else_bb or after_bb)
    codegen.builder.cbranch(cond0, body_bbs[0], false0)

    for i in range(1, n):
        codegen.builder.position_at_end(test_bbs[i - 1])
        cond_i = codegen.utils.as_i1(codegen.expressions.emit_expr(arms[i][0], to_i1=True))
        false_i = test_bbs[i] if (i + 1) < n else (else_bb or after_bb)
        codegen.builder.cbranch(cond_i, body_bbs[i], false_i)

    for i, (_, arm_block) in enumerate(arms):
        codegen.builder.position_at_end(body_bbs[i])
        codegen.memory.push_scope()
        _emit_block(codegen, arm_block)
        codegen.memory.pop_scope()
        if codegen.builder.block.terminator is None:
            codegen.builder.branch(after_bb)

    if else_bb is not None:
        codegen.builder.position_at_end(else_bb)
        codegen.memory.push_scope()
        if node.else_block is None:
            raise_internal_error("CE0015", message="else_bb exists but else_block is None")
        _emit_block(codegen, node.else_block)
        codegen.memory.pop_scope()
        if codegen.builder.block.terminator is None:
            codegen.builder.branch(after_bb)

    codegen.builder.position_at_end(after_bb)


def emit_while(codegen: 'LLVMCodegen', node: 'While') -> None:
    """Emit while loop with proper basic block structure.

    Creates condition, body, and end blocks with proper loop context
    for break/continue statements.

    Args:
        codegen: The main LLVMCodegen instance.
        node: The while statement node to emit.
    """
    builder, func = require_both_initialized(codegen)
    codegen.utils.ensure_open_block()

    cond_bb = codegen.func.append_basic_block(name="while.cond")
    body_bb = codegen.func.append_basic_block(name="while.body")
    end_bb = codegen.func.append_basic_block(name="while.end")

    codegen.builder.branch(cond_bb)

    codegen.builder.position_at_end(cond_bb)
    cond_val = codegen.utils.as_i1(codegen.expressions.emit_expr(node.cond))
    codegen.builder.cbranch(cond_val, body_bb, end_bb)

    codegen.builder.position_at_end(body_bb)
    codegen.loop_stack.append((cond_bb, end_bb))
    codegen.memory.push_scope()
    _emit_block(codegen, node.body)
    codegen.memory.pop_scope()
    codegen.loop_stack.pop()
    if codegen.builder.block.terminator is None:
        codegen.builder.branch(cond_bb)

    codegen.builder.position_at_end(end_bb)


def _emit_block(codegen: 'LLVMCodegen', block) -> None:
    """Helper to emit a block of statements.

    Args:
        codegen: The main LLVMCodegen instance.
        block: The block AST node containing statements.
    """
    # Import here to avoid circular dependency
    from backend.statements import StatementEmitter
    emitter = StatementEmitter(codegen)
    emitter.emit_block(block)
