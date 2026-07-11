"""One counted walk over contiguous elements, for the containers that all do it.

List and HashMap each walk `data[0..count)` to destroy elements, to initialise
slots, or to print them. The loop is the same every time -- a counter alloca, a
`i < count` header, a GEP, a body, an increment -- and it was written out by hand
at nine sites, which is how HashMap.free and HashMap.destroy ended up as
byte-identical copies of each other.

The body is a callback so the caller keeps ownership of what happens per element,
and of the value it computes: this helper emits blocks, it does not produce a
result. Callbacks may append blocks of their own (emit_value_destructor does), so
the loop always branches from `codegen.builder.block` as it stands *after* a
callback returns, never from the block it positioned at before.
"""

from typing import Any, Callable, Optional

import llvmlite.ir as ir

from sushi_lang.backend import gep_utils


# (element_ptr, index) -> None. Emits the per-element body.
ElementFn = Callable[[ir.Value, ir.Value], None]

# (element_ptr, index) -> i1. True means "run the body for this element".
PredicateFn = Callable[[ir.Value, ir.Value], ir.Value]


def emit_container_walk(
    codegen: Any,
    data_ptr: ir.Value,
    count: ir.Value,
    on_element: ElementFn,
    *,
    should_visit: Optional[PredicateFn] = None,
    null_guard: bool = False,
    prefix: str = "walk",
) -> None:
    """Walk `data_ptr[0..count)`, calling `on_element` for each element.

    Leaves the builder positioned after the loop.

    Args:
        codegen: LLVM codegen instance.
        data_ptr: Pointer to the first element (T*).
        count: How many elements to walk (i32).
        on_element: Emits the body for one element. Must not terminate its block.
        should_visit: Optional filter. Given the element, returns an i1; the body
            runs only when it is true. HashMap uses this to visit occupied slots
            only. May emit instructions (the state load, the comparison).
        null_guard: Wrap the whole loop in `if data_ptr != null`. Needed where the
            container may already have been emptied.
        prefix: Block-name prefix, so nested walks stay readable in the IR.
    """
    builder = codegen.builder

    if null_guard:
        null = ir.Constant(data_ptr.type, None)
        is_not_null = builder.icmp_unsigned("!=", data_ptr, null, name=f"{prefix}_not_null")
        with builder.if_then(is_not_null):
            _emit_walk(codegen, data_ptr, count, on_element, should_visit, prefix)
        return

    _emit_walk(codegen, data_ptr, count, on_element, should_visit, prefix)


def _emit_walk(
    codegen: Any,
    data_ptr: ir.Value,
    count: ir.Value,
    on_element: ElementFn,
    should_visit: Optional[PredicateFn],
    prefix: str,
) -> None:
    builder = codegen.builder
    i32 = codegen.types.i32
    zero = ir.Constant(i32, 0)
    one = ir.Constant(i32, 1)

    index_slot = builder.alloca(i32, name=f"{prefix}_i")
    builder.store(zero, index_slot)

    cond_bb = builder.append_basic_block(name=f"{prefix}_cond")
    body_bb = builder.append_basic_block(name=f"{prefix}_body")
    next_bb = builder.append_basic_block(name=f"{prefix}_next")
    end_bb = builder.append_basic_block(name=f"{prefix}_end")

    builder.branch(cond_bb)

    # i < count
    builder.position_at_end(cond_bb)
    index = builder.load(index_slot, name=f"{prefix}_i_val")
    builder.cbranch(
        builder.icmp_unsigned("<", index, count, name=f"{prefix}_cond"),
        body_bb,
        end_bb,
    )

    builder.position_at_end(body_bb)
    index = builder.load(index_slot, name=f"{prefix}_i_val")
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index, f"{prefix}_element_ptr")

    if should_visit is None:
        on_element(element_ptr, index)
        # The body may have appended blocks; branch from wherever we now are.
        builder.branch(next_bb)
    else:
        visit = should_visit(element_ptr, index)
        visit_bb = builder.append_basic_block(name=f"{prefix}_visit")
        builder.cbranch(visit, visit_bb, next_bb)

        builder.position_at_end(visit_bb)
        on_element(element_ptr, index)
        builder.branch(next_bb)

    # i += 1
    builder.position_at_end(next_bb)
    index = builder.load(index_slot, name=f"{prefix}_i_val")
    builder.store(builder.add(index, one, name=f"{prefix}_i_next"), index_slot)
    builder.branch(cond_bb)

    builder.position_at_end(end_bb)
