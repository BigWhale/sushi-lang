"""Shared utilities for statement emission in the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING
from sushi_lang.backend.utils import require_function

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_condition(codegen: 'LLVMCodegen', expr) -> 'ir.Value':
    """Emit a boolean condition.

    A condition is a bool since #522, so no wrapper temporary can stand here any more.
    The Result/Maybe temporary this used to free (#159) is now behind the `.is_ok()`
    that tests it, where the ordinary method-call temp registry owns it.
    """
    return codegen.utils.as_i1(codegen.expressions.emit_expr(expr))


def emit_scope_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit the destructors of every open scope on a function exit (return, `??`)."""
    codegen.memory.emit_exit_cleanup(0)

    # A `??` inside a print argument leaves through here, not the frame's straight-line
    # pop, so the buffers built before the propagation had no free at all (#295).
    codegen.emit_string_temp_frame_cleanup_all()


def create_loop_blocks(codegen: 'LLVMCodegen', prefix: str = "loop") -> tuple['ir.Block', 'ir.Block', 'ir.Block']:
    """Create standard loop basic blocks (condition, body, end)."""
    require_function(codegen)
    cond_bb = codegen.func.append_basic_block(name=f"{prefix}.cond")
    body_bb = codegen.func.append_basic_block(name=f"{prefix}.body")
    end_bb = codegen.func.append_basic_block(name=f"{prefix}.end")
    return cond_bb, body_bb, end_bb


def create_conditional_blocks(
    codegen: 'LLVMCodegen',
    prefix: str,
    num_arms: int,
    has_else: bool = False
) -> tuple[list['ir.Block'], 'ir.Block', 'ir.Block | None']:
    """Create basic blocks for conditional statements (if/match)."""
    require_function(codegen)
    arm_blocks = [codegen.func.append_basic_block(name=f"{prefix}.arm{i}") for i in range(num_arms)]
    end_block = codegen.func.append_basic_block(name=f"{prefix}.end")
    else_block = codegen.func.append_basic_block(name=f"{prefix}.else") if has_else else None
    return arm_blocks, end_block, else_block


def emit_block_with_scope(codegen: 'LLVMCodegen', block, emit_func=None) -> None:
    """Emit a block with automatic scope management."""
    codegen.memory.push_scope()
    if emit_func:
        emit_func(block)
    else:
        from sushi_lang.backend.statements import StatementEmitter
        emitter = StatementEmitter(codegen)
        emitter.emit_block(block)
    codegen.memory.pop_scope()


def emit_copy_loop(
    codegen: 'LLVMCodegen',
    count: 'ir.Value',
    src_ptr: 'ir.Value',
    dst_ptr: 'ir.Value',
    element_type: 'ir.Type',
    name_prefix: str = "copy"
) -> None:
    """Generate a simple loop to copy elements from src to dst."""
    from llvmlite import ir

    zero = ir.Constant(codegen.i32, 0)
    one = ir.Constant(codegen.i32, 1)

    index_ptr = codegen.memory.entry_alloca(codegen.i32, f"{name_prefix}_index")
    codegen.builder.store(zero, index_ptr)

    loop_head = codegen.builder.append_basic_block(f'{name_prefix}_loop_head')
    loop_body = codegen.builder.append_basic_block(f'{name_prefix}_loop_body')
    loop_done = codegen.builder.append_basic_block(f'{name_prefix}_loop_done')

    codegen.builder.branch(loop_head)

    codegen.builder.position_at_end(loop_head)
    current_index = codegen.builder.load(index_ptr)
    loop_continue = codegen.builder.icmp_signed('<', current_index, count)
    codegen.builder.cbranch(loop_continue, loop_body, loop_done)

    codegen.builder.position_at_end(loop_body)
    src_elem_ptr = codegen.builder.gep(src_ptr, [current_index])
    dst_elem_ptr = codegen.builder.gep(dst_ptr, [current_index])
    elem_value = codegen.builder.load(src_elem_ptr)
    codegen.builder.store(elem_value, dst_elem_ptr)

    next_index = codegen.builder.add(current_index, one)
    codegen.builder.store(next_index, index_ptr)
    codegen.builder.branch(loop_head)

    codegen.builder.position_at_end(loop_done)
