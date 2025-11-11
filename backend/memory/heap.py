"""
Heap allocation operations with error handling.

Provides malloc/free wrappers with runtime error checking for allocation failures.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


def emit_malloc(codegen: 'LLVMCodegen', builder: ir.IRBuilder, size_bytes: ir.Value) -> ir.Value:
    """Emit malloc call with error checking.

    Args:
        codegen: The LLVM code generator instance.
        builder: The IR builder for emitting instructions.
        size_bytes: LLVM value (i32 or i64) representing allocation size.

    Returns:
        Pointer to allocated memory (void*).

    Raises:
        RuntimeError: Emits RE2021 runtime error if allocation fails.
    """
    # Get malloc function from centralized codegen
    malloc_func = codegen.get_malloc_func()

    # Convert size to i64 if needed (malloc expects size_t)
    if size_bytes.type == ir.IntType(INT32_BIT_WIDTH):
        size_val = builder.zext(size_bytes, ir.IntType(INT64_BIT_WIDTH), name="size_i64")
    else:
        size_val = size_bytes

    # Call malloc
    result = builder.call(malloc_func, [size_val], name="malloc_result")

    # Check if malloc returned NULL (allocation failure)
    null_ptr = ir.Constant(ir.PointerType(ir.IntType(INT8_BIT_WIDTH)), None)
    is_null = builder.icmp_unsigned('==', result, null_ptr, name="is_null")

    # Create basic blocks for null check
    null_block = builder.append_basic_block(name="malloc_null")
    success_block = builder.append_basic_block(name="malloc_success")

    # Branch based on null check
    builder.cbranch(is_null, null_block, success_block)

    # Null block: emit runtime error and exit
    builder.position_at_end(null_block)
    codegen.runtime.errors.emit_runtime_error("RE2021", "memory allocation failed")
    # emit_runtime_error calls exit(), so this block is terminated
    # Add unreachable to satisfy LLVM
    builder.unreachable()

    # Success block: continue normal execution
    builder.position_at_end(success_block)

    return result


def emit_free(builder: ir.IRBuilder, codegen: 'LLVMCodegen', ptr: ir.Value) -> None:
    """Emit free call for the given pointer.

    Args:
        builder: The IR builder for emitting instructions.
        codegen: The LLVM code generator instance.
        ptr: The pointer to free (void*).
    """
    # Get free function from centralized codegen
    free_func = codegen.get_free_func()
    builder.call(free_func, [ptr])
