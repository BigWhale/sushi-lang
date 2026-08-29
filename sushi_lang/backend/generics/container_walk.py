"""One counted walk over contiguous elements, for the containers that all do it."""

from typing import Any, Callable, Optional

import llvmlite.ir as ir

from sushi_lang.backend import gep_utils


ElementFn = Callable[[ir.Value, ir.Value], None]

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
    """Walk `data_ptr[0..count)`, calling `on_element` for each element."""
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
        builder.branch(next_bb)
    else:
        visit = should_visit(element_ptr, index)
        visit_bb = builder.append_basic_block(name=f"{prefix}_visit")
        builder.cbranch(visit, visit_bb, next_bb)

        builder.position_at_end(visit_bb)
        on_element(element_ptr, index)
        builder.branch(next_bb)

    builder.position_at_end(next_bb)
    index = builder.load(index_slot, name=f"{prefix}_i_val")
    builder.store(builder.add(index, one, name=f"{prefix}_i_next"), index_slot)
    builder.branch(cond_bb)

    builder.position_at_end(end_bb)


def emit_container_search(
    codegen: Any,
    data_ptr: ir.Value,
    count: ir.Value,
    matches: PredicateFn,
    *,
    prefix: str = "search",
) -> tuple[ir.Value, ir.Value]:
    """Walk `data_ptr[0..count)` until `matches(element_ptr, index)` answers true.

    Returns `(found i1, index i32)`; the index means something only when found is
    true. The walk above visits every element; a SEARCH stops at the first hit, so
    the early exit lives here, next to the walk, and not in a caller's private copy
    of the loop. `matches` may open blocks of its own (a string compare does): the
    branch on its answer reads the builder's CURRENT block, not the body block.
    """
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

    builder.position_at_end(cond_bb)
    index = builder.load(index_slot, name=f"{prefix}_index")
    in_range = builder.icmp_signed("<", index, count, name=f"{prefix}_in_range")
    builder.cbranch(in_range, body_bb, end_bb)

    builder.position_at_end(body_bb)
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index, f"{prefix}_elem")
    hit = matches(element_ptr, index)
    hit_pred = builder.block
    builder.cbranch(hit, end_bb, next_bb)

    builder.position_at_end(next_bb)
    builder.store(builder.add(index, one, name=f"{prefix}_inc"), index_slot)
    builder.branch(cond_bb)

    builder.position_at_end(end_bb)
    found = builder.phi(ir.IntType(1), name=f"{prefix}_found")
    found.add_incoming(ir.Constant(ir.IntType(1), 0), cond_bb)
    found.add_incoming(ir.Constant(ir.IntType(1), 1), hit_pred)
    found_index = builder.load(index_slot, name=f"{prefix}_found_index")
    return found, found_index
