"""String Helper Functions"""

import llvmlite.ir as ir
from sushi_lang.backend.string_layout import string_value


ALLOC_FAIL_BLOCK = "alloc_fail"


def alloc_fail_block(func: ir.Function) -> ir.Block:
    """The one RE2021 block of `func`. The first request emits it."""
    for block in func.blocks:
        if block.name == ALLOC_FAIL_BLOCK:
            return block
    from .error_emission import emit_runtime_error
    block = func.append_basic_block(name=ALLOC_FAIL_BLOCK)
    emit_runtime_error(func.module, ir.IRBuilder(block), "RE2021")
    return block


def emit_checked_malloc(builder: ir.IRBuilder, malloc_fn: ir.Function, size: ir.Value,
                        name: str = "") -> ir.Value:
    """Call malloc(size), and go to the RE2021 block of the function on a null answer.

    The builder continues at the end of a new block, so a phi after this call must name
    `builder.block` as its predecessor, not the block the call started in.
    """
    return _null_checked(builder, builder.call(malloc_fn, [size], name=name))


def emit_checked_realloc(builder: ir.IRBuilder, realloc_fn: ir.Function, ptr: ir.Value,
                         size: ir.Value, name: str = "") -> ir.Value:
    """Call realloc(ptr, size), checked as `emit_checked_malloc` is. `size` must not be 0."""
    return _null_checked(builder, builder.call(realloc_fn, [ptr, size], name=name))


def _null_checked(builder: ir.IRBuilder, raw: ir.Value) -> ir.Value:
    func = builder.function
    fail = alloc_fail_block(func)
    ok = func.append_basic_block(name="alloc_ok")
    failed = builder.icmp_unsigned("==", raw, ir.Constant(raw.type, None), name="alloc_failed")
    builder.cbranch(failed, fail, ok)
    builder.position_at_end(ok)
    return raw


def cstr_to_fat_pointer_with_len(
    builder: ir.IRBuilder,
    c_str: ir.Value,
    length: ir.Value,
    owned: int,
) -> ir.Value:
    """Convert C string to fat pointer struct using pre-computed length."""
    return string_value(builder, c_str, length, owned, name="str_complete")
