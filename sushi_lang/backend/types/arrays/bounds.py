"""
Shared runtime bounds-check emission for array element access.

A single `emit_bounds_check` replaces four near-identical copies of the
`index >= 0 && index < size` guard (fixed-array indexing, dynamic-array indexing,
and both `.get()` Maybe paths). The default failure action traps with RE2020; a
caller may pass `on_fail` to branch elsewhere (e.g. return Maybe.None).
"""
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
    """Emit an `index >= 0 && index < size` guard around the current position.

    On success the builder is left positioned at the ok-block, ready for the
    caller to emit the element access. On failure the ok/fail split runs
    `on_fail` (default: trap with RE2020 and `unreachable`).

    Args:
        codegen: The LLVM codegen instance.
        index_value: The index being accessed (i32).
        size_value: The array length / element count (i32).
        prefix: Basic-block name prefix, to keep IR readable.
        on_fail: Optional callback emitting the failure path. It runs with the
            builder positioned at the fail-block and is responsible for
            terminating it (branch or unreachable). Defaults to the RE2020 trap.
    """
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
            "array index %d out of bounds for array of size %d",
            index_value,
            size_value,
        )
        # emit_runtime_error_with_values calls exit(); terminate for LLVM.
        builder.unreachable()
    else:
        on_fail()

    builder.position_at_end(ok_block)
