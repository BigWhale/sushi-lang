"""Heap allocation operations with error handling."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_malloc(codegen: 'LLVMCodegen', builder: ir.IRBuilder, size_bytes: ir.Value) -> ir.Value:
    """Emit malloc call with error checking."""
    malloc_func = codegen._get_malloc_func()

    if size_bytes.type == ir.IntType(INT32_BIT_WIDTH):
        size_val = builder.zext(size_bytes, ir.IntType(INT64_BIT_WIDTH), name="size_i64")
    else:
        size_val = size_bytes

    result = builder.call(malloc_func, [size_val], name="malloc_result")

    null_ptr = ir.Constant(ir.PointerType(ir.IntType(INT8_BIT_WIDTH)), None)
    is_null = builder.icmp_unsigned('==', result, null_ptr, name="is_null")

    null_block = builder.append_basic_block(name="malloc_null")
    success_block = builder.append_basic_block(name="malloc_success")

    builder.cbranch(is_null, null_block, success_block)

    builder.position_at_end(null_block)
    codegen.runtime.errors.emit_runtime_error("RE2021")
    builder.unreachable()

    builder.position_at_end(success_block)

    return result


def emit_free(builder: ir.IRBuilder, codegen: 'LLVMCodegen', ptr: ir.Value) -> None:
    """Emit free call for the given pointer."""
    free_func = codegen.get_free_func()
    builder.call(free_func, [ptr])
