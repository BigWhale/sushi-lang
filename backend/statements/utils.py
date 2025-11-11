"""
Shared utilities for statement emission in the Sushi language compiler.

This module provides helper functions for common patterns used across statement
emitters, including RAII cleanup, basic block management, scope handling, and
GEP operations.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from internals.errors import raise_internal_error

if TYPE_CHECKING:
    from llvmlite import ir
    from backend.codegen_llvm import LLVMCodegen


# ============================================================================
# RAII Cleanup Helpers
# ============================================================================

def emit_struct_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit cleanup code for struct fields with dynamic arrays.

    Iterates through all scopes from innermost to outermost and emits cleanup
    for struct variables that have dynamic array fields.

    Move semantics: Skips cleanup for variables marked as moved.

    Args:
        codegen: The main LLVMCodegen instance.
    """
    if not hasattr(codegen, 'dynamic_arrays') or codegen.dynamic_arrays is None:
        return

    # Iterate through all scopes from innermost to outermost
    for scope_idx in range(len(codegen.memory.struct_variables) - 1, -1, -1):
        struct_scope = codegen.memory.struct_variables[scope_idx]
        for var_name, (struct_type, alloca) in struct_scope.items():
            # Skip cleanup if:
            # 1. Already cleaned
            # 2. Marked as moved (ownership transferred)
            if (var_name not in codegen.memory.cleaned_up_structs and
                not codegen.memory.is_struct_moved(var_name)):
                codegen.dynamic_arrays.emit_struct_field_cleanup(var_name, struct_type, alloca)
                codegen.memory.cleaned_up_structs.add(var_name)


def emit_dynamic_array_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit cleanup code for top-level dynamic arrays.

    Iterates through all dynamic array scopes and emits destructors for
    arrays that haven't been destroyed yet.

    Args:
        codegen: The main LLVMCodegen instance.
    """
    if not hasattr(codegen, 'dynamic_arrays') or codegen.dynamic_arrays is None:
        return

    # Iterate through all dynamic array scopes from innermost to outermost
    for scope_idx in range(len(codegen.dynamic_arrays.scope_stack) - 1, -1, -1):
        array_scope = codegen.dynamic_arrays.scope_stack[scope_idx]
        for array_name in array_scope:
            if array_name in codegen.dynamic_arrays.arrays:
                descriptor = codegen.dynamic_arrays.arrays[array_name]
                if not descriptor.destroyed:
                    codegen.dynamic_arrays._emit_array_destructor(array_name)
                    descriptor.destroyed = True


def emit_own_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit cleanup code for Own<T> variables.

    Generates Own<T>.destroy() calls for all registered Own<T> variables.

    Args:
        codegen: The main LLVMCodegen instance.
    """
    if not hasattr(codegen, 'dynamic_arrays') or codegen.dynamic_arrays is None:
        return

    # Emit cleanup for all Own<T> variables
    codegen.dynamic_arrays.emit_own_cleanup()


def emit_scope_cleanup(codegen: 'LLVMCodegen', cleanup_type: str = 'all') -> None:
    """Emit cleanup code for resources in all scopes.

    This is the main entry point for RAII cleanup, used by return statements
    and other locations that need to clean up before exiting.

    Args:
        codegen: The main LLVMCodegen instance.
        cleanup_type: Type of cleanup to perform:
            - 'all': Clean structs, dynamic arrays, and Own<T> (default)
            - 'structs': Clean only struct fields
            - 'arrays': Clean only top-level dynamic arrays
            - 'owned': Clean only Own<T> variables

    Raises:
        ValueError: If cleanup_type is not recognized.
    """
    if cleanup_type not in ('all', 'structs', 'arrays', 'owned'):
        raise_internal_error("CE0062", type=cleanup_type)

    if cleanup_type in ('all', 'structs'):
        emit_struct_cleanup(codegen)

    if cleanup_type in ('all', 'arrays'):
        emit_dynamic_array_cleanup(codegen)

    if cleanup_type in ('all', 'owned'):
        emit_own_cleanup(codegen)


# ============================================================================
# GEP (GetElementPtr) Helpers
# ============================================================================

def gep_struct_field(codegen: 'LLVMCodegen', slot: 'ir.Value', field_index: int, name: str = None) -> 'ir.Value':
    """Create a GEP instruction to access a struct field.

    This function delegates to backend.gep_utils for centralized GEP logic.
    Kept for backward compatibility with existing code.

    Args:
        codegen: The main LLVMCodegen instance.
        slot: The pointer to the struct.
        field_index: The index of the field to access.
        name: Optional name for the GEP instruction.

    Returns:
        The GEP instruction (pointer to the field).

    Example:
        # Before: 3 lines
        zero = ir.Constant(codegen.i32, 0)
        len_ptr = codegen.builder.gep(slot, [zero, ir.Constant(codegen.i32, 0)])

        # After: 1 line
        len_ptr = gep_struct_field(codegen, slot, 0, "len_ptr")

    Note:
        New code should use backend.gep_utils.gep_struct_field() directly.
    """
    from backend import gep_utils
    return gep_utils.gep_struct_field(codegen, slot, field_index, name or "")


# ============================================================================
# Basic Block Management Helpers
# ============================================================================

def create_loop_blocks(codegen: 'LLVMCodegen', prefix: str = "loop") -> tuple['ir.Block', 'ir.Block', 'ir.Block']:
    """Create standard loop basic blocks (condition, body, end).

    Args:
        codegen: The main LLVMCodegen instance.
        prefix: Prefix for block names (default: "loop").

    Returns:
        Tuple of (cond_block, body_block, end_block).
    """
    if codegen.func is None:
        raise_internal_error("CE0010")
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
    """Create basic blocks for conditional statements (if/match).

    Args:
        codegen: The main LLVMCodegen instance.
        prefix: Prefix for block names (e.g., "if", "match").
        num_arms: Number of conditional arms.
        has_else: Whether there's an else block.

    Returns:
        Tuple of (arm_blocks, end_block, else_block).
        - arm_blocks: List of blocks for each arm
        - end_block: The merge block after the conditional
        - else_block: The else block (None if has_else=False)
    """
    if codegen.func is None:
        raise_internal_error("CE0010")
    arm_blocks = [codegen.func.append_basic_block(name=f"{prefix}.arm{i}") for i in range(num_arms)]
    end_block = codegen.func.append_basic_block(name=f"{prefix}.end")
    else_block = codegen.func.append_basic_block(name=f"{prefix}.else") if has_else else None
    return arm_blocks, end_block, else_block


# ============================================================================
# Scope Management Helpers
# ============================================================================

def emit_block_with_scope(codegen: 'LLVMCodegen', block, emit_func=None) -> None:
    """Emit a block with automatic scope management.

    Pushes a new scope, emits the block statements, and pops the scope.
    This is a common pattern used throughout statement emission.

    Args:
        codegen: The main LLVMCodegen instance.
        block: The block AST node to emit.
        emit_func: Optional custom emit function. If None, uses emit_block.
    """
    codegen.memory.push_scope()
    if emit_func:
        emit_func(block)
    else:
        # Import here to avoid circular dependency
        from backend.statements import StatementEmitter
        emitter = StatementEmitter(codegen)
        emitter.emit_block(block)
    codegen.memory.pop_scope()


# ============================================================================
# Loop Emission Helpers
# ============================================================================

def emit_copy_loop(
    codegen: 'LLVMCodegen',
    count: 'ir.Value',
    src_ptr: 'ir.Value',
    dst_ptr: 'ir.Value',
    element_type: 'ir.Type',
    name_prefix: str = "copy"
) -> None:
    """Generate a simple loop to copy elements from src to dst.

    This helper eliminates duplication for the common pattern of copying
    elements from one array to another using a simple index-based loop.

    Args:
        codegen: The main LLVMCodegen instance.
        count: Number of elements to copy (i32).
        src_ptr: Source pointer (T*).
        dst_ptr: Destination pointer (T*).
        element_type: LLVM element type (used for type checking, not GEP).
        name_prefix: Prefix for generated block names (default: "copy").

    Example usage:
        # Copy elements from source array to destination array
        emit_copy_loop(
            codegen=codegen,
            count=array_length,
            src_ptr=source_data_ptr,
            dst_ptr=dest_data_ptr,
            element_type=codegen.i32,
            name_prefix="clone"
        )
    """
    from llvmlite import ir

    zero = ir.Constant(codegen.i32, 0)
    one = ir.Constant(codegen.i32, 1)

    # Allocate loop counter
    index_ptr = codegen.memory.entry_alloca(codegen.i32, f"{name_prefix}_index")
    codegen.builder.store(zero, index_ptr)

    # Create blocks
    loop_head = codegen.builder.append_basic_block(f'{name_prefix}_loop_head')
    loop_body = codegen.builder.append_basic_block(f'{name_prefix}_loop_body')
    loop_done = codegen.builder.append_basic_block(f'{name_prefix}_loop_done')

    codegen.builder.branch(loop_head)

    # Loop condition: index < count
    codegen.builder.position_at_end(loop_head)
    current_index = codegen.builder.load(index_ptr)
    loop_continue = codegen.builder.icmp_signed('<', current_index, count)
    codegen.builder.cbranch(loop_continue, loop_body, loop_done)

    # Loop body: copy element
    codegen.builder.position_at_end(loop_body)
    src_elem_ptr = codegen.builder.gep(src_ptr, [current_index])
    dst_elem_ptr = codegen.builder.gep(dst_ptr, [current_index])
    elem_value = codegen.builder.load(src_elem_ptr)
    codegen.builder.store(elem_value, dst_elem_ptr)

    # Increment and continue
    next_index = codegen.builder.add(current_index, one)
    codegen.builder.store(next_index, index_ptr)
    codegen.builder.branch(loop_head)

    # Position at done block
    codegen.builder.position_at_end(loop_done)
