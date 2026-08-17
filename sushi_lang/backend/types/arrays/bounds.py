"""Shared runtime bounds-check emission for array element access."""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_bounds_check(
    codegen: 'LLVMCodegen',
    index_value: ir.Value,
    size_value: ir.Value,
    prefix: str = "array",
    on_fail: Optional[Callable[[], None]] = None,
) -> None:
    """Emit an `index >= 0 && index < size` guard around the current position."""
    builder = codegen.builder
    zero = ir.Constant(codegen.i32, 0)

    index_not_negative = builder.icmp_signed(">=", index_value, zero, name=f"{prefix}_not_negative")
    index_in_bounds = builder.icmp_unsigned("<", index_value, size_value, name=f"{prefix}_in_bounds")
    bounds_ok = builder.and_(index_not_negative, index_in_bounds, name=f"{prefix}_bounds_ok")

    ok_block = builder.append_basic_block(name=f"{prefix}_bounds_ok")
    fail_block = builder.append_basic_block(name=f"{prefix}_bounds_fail")
    builder.cbranch(bounds_ok, ok_block, fail_block)

    builder.position_at_end(fail_block)
    if on_fail is None:
        codegen.runtime.errors.emit_runtime_error_with_values(
            "RE2020",
            index_value,
            size_value,
        )
        # emit_runtime_error_with_values calls exit(); terminate for LLVM.
        builder.unreachable()
    else:
        on_fail()

    builder.position_at_end(ok_block)
