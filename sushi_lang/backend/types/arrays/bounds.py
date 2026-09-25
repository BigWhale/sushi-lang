"""Shared runtime bounds-check emission for array element access."""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_bounds_check(
    codegen: 'LLVMCodegen',
    index_value: ir.Value,
    size_value: ir.Value,
    prefix: str = "array",
    on_fail: Optional[Callable[[], None]] = None,
    *,
    inclusive: bool = False,
) -> None:
    """Emit an `index >= 0 && index < size` guard around the current position.

    `inclusive` admits `index == size` too: an insert position may be one past the end.
    """
    builder = codegen.builder
    zero = ir.Constant(codegen.i32, 0)

    index_not_negative = builder.icmp_signed(">=", index_value, zero, name=f"{prefix}_not_negative")
    index_in_bounds = builder.icmp_unsigned("<=" if inclusive else "<", index_value, size_value,
                                            name=f"{prefix}_in_bounds")
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
        builder.unreachable()
    else:
        on_fail()

    builder.position_at_end(ok_block)


def emit_checked_maybe(
    codegen: 'LLVMCodegen',
    index_value: ir.Value,
    size_value: ir.Value,
    element_type: 'Type',
    read_element: Callable[[], ir.Value],
    prefix: str = "get",
) -> ir.Value:
    """`Maybe.Some(read_element())` when `0 <= index < size`, else `Maybe.None`."""
    from sushi_lang.backend.generics.maybe import emit_maybe_some, emit_maybe_none

    builder = codegen.builder
    merge_block = codegen.func.append_basic_block(f"{prefix}_merge")
    none_state: dict = {}

    def on_fail() -> None:
        none_state["result"] = emit_maybe_none(codegen, element_type)
        none_state["pred"] = builder.block
        builder.branch(merge_block)

    emit_bounds_check(codegen, index_value, size_value, prefix=prefix, on_fail=on_fail)

    some_result = emit_maybe_some(codegen, element_type, read_element())
    some_pred = builder.block
    builder.branch(merge_block)

    builder.position_at_end(merge_block)
    result_phi = builder.phi(some_result.type, name=f"{prefix}_result")
    result_phi.add_incoming(some_result, some_pred)
    result_phi.add_incoming(none_state["result"], none_state["pred"])
    return result_phi
