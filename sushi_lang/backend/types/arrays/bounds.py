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

    The index and the size are sign-extended to i64, and one unsigned compare is the guard:
    a size is not negative, and a negative index read as unsigned is above every size. The
    index of an index position is an i64 (docs/adr/0001), so an index above 2^32 fails too,
    and RE2020 prints the two i64 values.

    `inclusive` admits `index == size` too: an insert position may be one past the end.
    """
    builder = codegen.builder
    index_value, size_value = to_i64(codegen, index_value), to_i64(codegen, size_value)

    bounds_ok = builder.icmp_unsigned("<=" if inclusive else "<", index_value, size_value,
                                      name=f"{prefix}_in_bounds")

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


def to_i64(codegen: 'LLVMCodegen', value: ir.Value) -> ir.Value:
    """`value`, a signed integer, sign-extended to i64."""
    i64 = ir.IntType(64)
    return value if value.type == i64 else codegen.builder.sext(value, i64)


def checked_i32(codegen: 'LLVMCodegen', value: ir.Value) -> ir.Value:
    """An index that a bounds check passed, as an i32 for the arithmetic of an i32 length:
    the check proved `0 <= value <= size`, so the truncation loses nothing."""
    i32 = codegen.types.i32
    return value if value.type == i32 else codegen.builder.trunc(value, i32)


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


def emit_checked_maybe_into(
    codegen: 'LLVMCodegen',
    index_value: ir.Value,
    size_value: ir.Value,
    element_type: 'Type',
    element_address: Callable[[], ir.Value],
    dest: ir.Value,
    prefix: str = "get",
) -> None:
    """`Maybe.Some` of the bytes at `element_address()` when `0 <= index < size`, else
    `Maybe.None`, written into the slot `dest`. Each branch writes `dest`: no `phi`."""
    from sushi_lang.backend.generics.maybe import emit_maybe_into

    builder = codegen.builder
    merge_block = codegen.func.append_basic_block(f"{prefix}_merge")

    def on_fail() -> None:
        emit_maybe_into(codegen, element_type, dest)
        builder.branch(merge_block)

    emit_bounds_check(codegen, index_value, size_value, prefix=prefix, on_fail=on_fail)
    emit_maybe_into(codegen, element_type, dest, element_address())
    builder.branch(merge_block)
    builder.position_at_end(merge_block)
