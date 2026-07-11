"""
Shared utilities for statement emission in the Sushi language compiler.

This module provides helper functions for common patterns used across statement
emitters, including RAII cleanup, basic block management, scope handling, and
GEP operations.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_function

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


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

    # Iterate through all scopes from innermost to outermost. This runs on an early-exit
    # path (return / ?? / default return); the block terminates immediately after. Emit
    # the destructor for every live, non-moved struct WITHOUT marking it cleaned: each
    # exit path is a separate, mutually-exclusive basic block, so every path must emit its
    # own free (#59/#60). The structural pop_scope drains the tracking on the fall-through
    # path. Moved structs (ownership transferred to the caller) are skipped.
    for scope_idx in range(len(codegen.memory.struct_variables) - 1, -1, -1):
        struct_scope = codegen.memory.struct_variables[scope_idx]
        for var_name, (struct_type, alloca) in struct_scope.items():
            if not codegen.moves.is_moved(alloca):
                codegen.dynamic_arrays.emit_struct_field_cleanup(var_name, struct_type, alloca)


def emit_closure_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit runtime-guarded env frees for all live function-value locals (closures).

    Runs on an early-exit path (return / ?? / default return); the block terminates
    immediately after. Emit the guarded `if drop: drop(env)` for every live, non-moved
    closure local WITHOUT draining the tracking: each exit path is a separate,
    mutually-exclusive basic block, so every path frees on its own block and the
    structural pop_scope drains the fall-through path (#59/#60). Moved (escaped)
    closures are skipped -- their new owner frees the env.
    """
    mem = getattr(codegen, 'memory', None)
    if mem is None or not getattr(mem, '_closure_cleanup', None):
        return
    for var_name, entries in mem._closure_cleanup.items():
        for _depth, slot in entries:
            if not codegen.moves.is_moved(slot):
                mem._emit_closure_free(slot)


def emit_dynamic_array_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit cleanup code for top-level dynamic arrays.

    Iterates through all dynamic array scopes and emits destructors for
    arrays that haven't been destroyed yet.

    Args:
        codegen: The main LLVMCodegen instance.
    """
    if not hasattr(codegen, 'dynamic_arrays') or codegen.dynamic_arrays is None:
        return

    # Iterate through all dynamic array scopes from innermost to outermost.
    # This runs on an early-exit path (return / ?? / default return); the block
    # terminates immediately after. Emit the destructor for every live array
    # WITHOUT marking it globally destroyed: each exit path is a separate, mutually
    # exclusive basic block, so every path must emit its own free. The `destroyed`
    # flag means "explicitly .destroy()'d" (a permanent, cross-path state) and must
    # not be set here, or later exit paths would skip the free and leak (#59). The
    # structural pop_scope drains the tracking on the fall-through path.
    for scope_idx in range(len(codegen.dynamic_arrays.scope_stack) - 1, -1, -1):
        array_scope = codegen.dynamic_arrays.scope_stack[scope_idx]
        for array_name in array_scope:
            if array_name in codegen.dynamic_arrays.arrays:
                # _emit_array_destructor is a no-op for moved / explicitly-destroyed arrays.
                codegen.dynamic_arrays._emit_array_destructor(array_name)


def emit_list_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit cleanup code for local List<T> variables (#61).

    Iterates all List<T> scopes from innermost to outermost and emits destructors for
    lists that have not been moved / explicitly destroyed. Runs on an early-exit path
    (return / ?? / default return); the block terminates immediately after, so each
    mutually-exclusive exit path frees on its own block WITHOUT marking the list
    destroyed -- the structural pop_scope drains the tracking on the fall-through path.

    Args:
        codegen: The main LLVMCodegen instance.
    """
    if not hasattr(codegen, 'dynamic_arrays') or codegen.dynamic_arrays is None:
        return

    for scope_idx in range(len(codegen.dynamic_arrays.list_scope_stack) - 1, -1, -1):
        for list_name in codegen.dynamic_arrays.list_scope_stack[scope_idx]:
            codegen.dynamic_arrays._emit_list_destructor(list_name)


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


def emit_loop_exit_cleanup(codegen: 'LLVMCodegen', min_scope_index: int) -> None:
    """Emit RAII destructors for the loop's own scopes on a break/continue path.

    Frees heap-owning locals -- dynamic arrays, List<T>, struct dynamic-array fields,
    Own<T>, and marshalled C strings -- declared at scope index >= min_scope_index, i.e.
    the loop-body scope captured at loop entry plus any nested if/match scopes inside it.
    Enclosing (post-loop) scopes are deliberately left intact, so a local that outlives
    the loop is not double-freed.

    Like emit_scope_cleanup, this emits WITHOUT draining the tracking: each break/continue
    block terminates immediately after and is mutually exclusive at runtime with the other
    exit paths (return / ?? / fall-through), so every path frees exactly once and the
    structural pop_scope drains the fall-through path (#59/#60). It differs only in being
    bounded to the loop's scopes rather than the whole function.
    """
    da = getattr(codegen, 'dynamic_arrays', None)
    if da is None:
        return
    mem = codegen.memory

    # Dynamic arrays and List<T> live in per-scope stacks whose indices align with the
    # memory scope depth captured as min_scope_index.
    for scope_idx in range(len(da.scope_stack) - 1, min_scope_index - 1, -1):
        for array_name in da.scope_stack[scope_idx]:
            if array_name in da.arrays:
                da._emit_array_destructor(array_name)
    for scope_idx in range(len(da.list_scope_stack) - 1, min_scope_index - 1, -1):
        for list_name in da.list_scope_stack[scope_idx]:
            da._emit_list_destructor(list_name)

    # Struct dynamic-array fields, Own<T>, and closures are tracked in stacked maps keyed by
    # name; use the per-scope variable sets to bound them to the loop's scopes, matching each
    # binding to the entry registered at its own scope level so a shadow is not confused with
    # its namesake and the move check uses that binding's exact slot.
    for scope_idx in range(len(mem._scope_vars) - 1, min_scope_index - 1, -1):
        for var_name in mem._scope_vars[scope_idx]:
            for entry in mem._struct_cleanup.get(var_name, ()):
                if entry[0] == scope_idx:
                    _d, struct_type, alloca = entry
                    if not codegen.moves.is_moved(alloca):
                        da.emit_struct_field_cleanup(var_name, struct_type, alloca)
                    break
            for entry in mem._closure_cleanup.get(var_name, ()):
                if entry[0] == scope_idx:
                    if not codegen.moves.is_moved(entry[-1]):
                        mem._emit_closure_free(entry[-1])
                    break
            # String locals (#145): owned-bit-guarded free, bounded to the loop's scopes.
            for entry in mem._string_cleanup.get(var_name, ()):
                if entry[0] == scope_idx:
                    if not codegen.moves.is_moved(entry[-1]):
                        mem._emit_string_free(entry[-1])
                    break
            descriptor = da.owned_pointers.get(var_name)
            if (descriptor is not None and descriptor.depth == scope_idx
                    and not descriptor.destroyed and not codegen.moves.is_moved(descriptor.slot)):
                da._emit_own_destructor(var_name, descriptor.own_type)

    # FFI marshalled C strings (per-scope).
    for scope_idx in range(len(mem._cstr_cleanup) - 1, min_scope_index - 1, -1):
        mem._free_cstr_list(mem._cstr_cleanup[scope_idx])

    # Inline-closure argument temporaries (#123), per-scope.
    closure_temps = getattr(mem, '_closure_temp_cleanup', None)
    if closure_temps is not None:
        for scope_idx in range(len(closure_temps) - 1, min_scope_index - 1, -1):
            mem._free_closure_temp_list(closure_temps[scope_idx])


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

    if cleanup_type == 'all':
        emit_closure_cleanup(codegen)

    if cleanup_type in ('all', 'arrays'):
        emit_dynamic_array_cleanup(codegen)
        emit_list_cleanup(codegen)

    if cleanup_type in ('all', 'owned'):
        emit_own_cleanup(codegen)

    # FFI no-leak: free marshalled C strings across all open scopes on early-exit
    # paths (return, ?? propagation). Emits frees into the current (terminating)
    # block WITHOUT mutating the registry, so each mutually-exclusive exit block
    # (including the fall-through pop_scope) frees exactly once on its own path.
    if cleanup_type == 'all' and hasattr(codegen, 'memory') and codegen.memory is not None:
        if hasattr(codegen.memory, 'emit_cstr_cleanup_all'):
            codegen.memory.emit_cstr_cleanup_all()
        # Inline-closure argument temporaries (#123): same early-exit discipline.
        if hasattr(codegen.memory, 'emit_closure_temp_cleanup_all'):
            codegen.memory.emit_closure_temp_cleanup_all()
        # String-value RAII (#145): owned-bit-guarded free of live string locals, same
        # early-exit discipline (moved strings skipped so their new owner frees them).
        if hasattr(codegen.memory, 'emit_string_cleanup_all'):
            codegen.memory.emit_string_cleanup_all()


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
    func = require_function(codegen)
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
    func = require_function(codegen)
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
        from sushi_lang.backend.statements import StatementEmitter
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
