"""Shared utilities for statement emission in the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING
from sushi_lang.internals.errors import raise_internal_error
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


def emit_struct_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit cleanup code for struct fields with dynamic arrays."""
    if not hasattr(codegen, 'dynamic_arrays') or codegen.dynamic_arrays is None:
        return

    # An early-exit path. Emit the destructor for every live, non-moved struct WITHOUT
    # marking it cleaned: each exit path is a separate, mutually-exclusive block, so every
    # one must emit its own free (#59/#60). `pop_scope` drains the tracking.
    #
    # Innermost scope first, and REVERSE declaration order within each -- the same order
    # the fall-through drain uses, or a `return` inside a block would destroy in the
    # opposite order to falling off the end of it.
    for scope_idx in range(len(codegen.memory.struct_variables) - 1, -1, -1):
        struct_scope = codegen.memory.struct_variables[scope_idx]
        for var_name, (struct_type, alloca) in reversed(list(struct_scope.items())):
            codegen.moves.emit_free_unless_moved(
                alloca,
                lambda n=var_name, t=struct_type, a=alloca:
                    codegen.dynamic_arrays.emit_struct_field_cleanup(n, t, a))


def emit_closure_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit runtime-guarded env frees for all live function-value locals (closures)."""
    mem = getattr(codegen, 'memory', None)
    if mem is None or not getattr(mem, '_closure_cleanup', None):
        return
    for _var_name, entries in mem._closure_cleanup.items():
        for _depth, slot in entries:
            codegen.moves.emit_free_unless_moved(
                slot, lambda s=slot: mem._emit_closure_free(s))


def emit_dynamic_array_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit cleanup code for top-level dynamic arrays."""
    if not hasattr(codegen, 'dynamic_arrays') or codegen.dynamic_arrays is None:
        return

    # The same discipline as the struct sweep above. `destroyed` must NOT be set here: it
    # means "explicitly .destroy()'d", a permanent cross-path state, so setting it would
    # make later exit paths skip the free and leak (#59).
    for scope_idx in range(len(codegen.dynamic_arrays.scope_stack) - 1, -1, -1):
        array_scope = codegen.dynamic_arrays.scope_stack[scope_idx]
        for array_name in reversed(array_scope):
            if array_name in codegen.dynamic_arrays.arrays:
                codegen.dynamic_arrays._emit_array_destructor(array_name)


def emit_list_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit cleanup code for local List<T> variables (#61)."""
    if not hasattr(codegen, 'dynamic_arrays') or codegen.dynamic_arrays is None:
        return

    for scope_idx in range(len(codegen.dynamic_arrays.list_scope_stack) - 1, -1, -1):
        for list_name in reversed(codegen.dynamic_arrays.list_scope_stack[scope_idx]):
            codegen.dynamic_arrays._emit_list_destructor(list_name)


def emit_own_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit cleanup code for Own<T> variables."""
    if not hasattr(codegen, 'dynamic_arrays') or codegen.dynamic_arrays is None:
        return

    codegen.dynamic_arrays.emit_own_cleanup()


def emit_loop_exit_cleanup(codegen: 'LLVMCodegen', min_scope_index: int) -> None:
    """Emit RAII destructors for the loop's own scopes on a break/continue path."""
    da = getattr(codegen, 'dynamic_arrays', None)
    if da is None:
        return
    mem = codegen.memory

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
                    codegen.moves.emit_free_unless_moved(
                        alloca,
                        lambda n=var_name, t=struct_type, a=alloca:
                            da.emit_struct_field_cleanup(n, t, a))
                    break
            for entry in mem._closure_cleanup.get(var_name, ()):
                if entry[0] == scope_idx:
                    codegen.moves.emit_free_unless_moved(
                        entry[-1], lambda s=entry[-1]: mem._emit_closure_free(s))
                    break
            # String locals (#145): owned-bit-guarded free, bounded to the loop's scopes.
            for entry in mem._string_cleanup.get(var_name, ()):
                if entry[0] == scope_idx:
                    codegen.moves.emit_free_unless_moved(
                        entry[-1], lambda s=entry[-1]: mem._emit_string_free(s))
                    break
            descriptor = da.owned_pointers.get(var_name)
            if (descriptor is not None and descriptor.depth == scope_idx
                    and not descriptor.destroyed):
                # _emit_own_destructor carries its own moved/flag gate (#414).
                da._emit_own_destructor(var_name, descriptor.own_type)

    for scope_idx in range(len(mem._cstr_cleanup) - 1, min_scope_index - 1, -1):
        mem._free_cstr_list(mem._cstr_cleanup[scope_idx])

    # Inline-closure argument temporaries (#123), per-scope.
    closure_temps = getattr(mem, '_closure_temp_cleanup', None)
    if closure_temps is not None:
        for scope_idx in range(len(closure_temps) - 1, min_scope_index - 1, -1):
            mem._free_closure_temp_list(closure_temps[scope_idx])


def emit_scope_cleanup(codegen: 'LLVMCodegen', cleanup_type: str = 'all') -> None:
    """Emit cleanup code for resources in all scopes."""
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

    # A `??` inside a print argument leaves through here, not the frame's straight-line
    # pop, so the buffers built before the propagation had no free at all (#295).
    if cleanup_type == 'all' and hasattr(codegen, 'emit_string_temp_frame_cleanup_all'):
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
