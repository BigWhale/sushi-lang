"""The adapters of `Iterator@(T)` (#1228, docs/design/iteration.md).

A LAZY adapter (`map`, `filter`, `enumerate`, `zip`, `clone_each`) answers a new iterator
value: its state holds what the adapter keeps (the iterator it reads, the function it
calls, a count), and its `next_fn` asks the inner iterator for an item and changes it. A
CONSUMER (`fold`, `partition`, `collect`) walks its receiver to the end at the call, in
the caller's function.

Every adapter takes its receiver and its arguments, as the operands of SLIR (docs/design/
ir.md 8.7): a lazy adapter is one row of the source table of `AggIterator`, and a consumer
is the row of the `iterator` family of the call emitter. The state of a lazy adapter is on
the heap when `heap` is true, and its `drop_fn` destroys what it holds and frees the block.
Else the state is a stack slot of the caller (an `entry_alloca`), and its `drop_fn`
destroys what the state holds and frees nothing. The borrow pass decided `heap`.

An item that an adapter does not pass on is destroyed in the parts that the item OWNS
(`destroy_owned_parts`): a lent part is a view of storage that the source keeps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from llvmlite import ir

from sushi_lang.backend import gep_utils
from sushi_lang.backend.runtime.iterators import (
    _clone_type, _drop_type, _function, _heap_state, _next_type, emit_iterator_clone,
    emit_next, iterator_value)
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.iterators import item_parts
from sushi_lang.semantics.typesys import FunctionType, IteratorType, StructType
from sushi_lang.backend.memory.allocas import entry_alloca

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type

# The field of the inner iterator in each state; the second field is the function, the
# count of `enumerate`, or the right side of `zip`.
_INNER, _SECOND = 0, 1

_SYMBOL_CHARS = {"<": "_L", ">": "_G", ",": "_C", "[": "_A", "]": "_E", " ": "_S",
                 "$": "0", "(": "_P", ")": "_Q", "-": "_M", "|": "_B", "@": "_T"}


def _key(*types: 'Type') -> str:
    """A symbol-safe key for the types that one adapter function is emitted for."""
    return "__".join("".join(ch if ch.isalnum() or ch == "_" else _SYMBOL_CHARS.get(ch, "_")
                             for ch in str(ty)) for ty in types)


# ---------------------------------------------------------------------------------------
# Items


def destroy_owned_parts(codegen: 'LLVMCodegen', item: ir.Value,
                        iterator_type: IteratorType) -> None:
    """Destroy what an item of `iterator_type` owns: the whole item, or each owned part of
    a tuple item. A lent part is left to its owner."""
    if not iterator_type.peeks:
        _destroy_value(codegen, item, iterator_type.element_type)
        return
    if len(iterator_type.peeks) == 1:
        return
    for index, (part, lent) in enumerate(zip(item_parts(iterator_type),
                                             iterator_type.peeks, strict=True)):
        if not lent:
            _destroy_value(codegen, codegen.builder.extract_value(item, index), part)


def _destroy_value(codegen: 'LLVMCodegen', value: ir.Value, value_type: 'Type') -> None:
    from sushi_lang.backend.destructors import emit_value_destructor, needs_cleanup
    if not needs_cleanup(codegen, value_type):
        return
    slot = entry_alloca(codegen.builder, value.type, "iter_item_drop")
    codegen.builder.store(value, slot)
    emit_value_destructor(codegen, slot, value_type)


def _destroy_held(codegen: 'LLVMCodegen', value: ir.Value) -> None:
    """Destroy an iterator or a function value through its own `drop` field."""
    from sushi_lang.backend.destructors import emit_function_value_destructor_from_value
    emit_function_value_destructor_from_value(codegen, value)


def _next_item(codegen: 'LLVMCodegen', iterator: ir.Value, item_type: 'Type',
               found: ir.Block, done: ir.Block) -> Callable[[], ir.Value]:
    """Ask `iterator` for its next item: branch to `found` or to `done`. The answer reads
    the item, at `found` only (the payload of a None means nothing)."""
    from sushi_lang.backend.constants.error_codes import MAYBE_SOME_TAG
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_tag_check
    answer = emit_next(codegen, iterator, item_type)
    has_item = emit_enum_tag_check(codegen, answer, MAYBE_SOME_TAG, "has_item")
    codegen.builder.cbranch(has_item, found, done)

    def item() -> ir.Value:
        _is_some, value = codegen.functions.extract_value_from_result_enum(
            answer, codegen.types.ll_type(item_type), item_type)
        return value
    return item


def _call(codegen: 'LLVMCodegen', function: ir.Value, fn_type: FunctionType,
          args: list) -> ir.Value:
    from sushi_lang.backend.runtime.closures import emit_indirect_call
    return emit_indirect_call(codegen, function, fn_type, args, to_i1=False)


def _tuple(codegen: 'LLVMCodegen', tuple_type: 'Type', parts: Sequence[ir.Value]) -> ir.Value:
    value = ir.Constant(codegen.types.ll_type(tuple_type), ir.Undefined)
    for index, part in enumerate(parts):
        value = codegen.builder.insert_value(value, part, index)
    return value


# ---------------------------------------------------------------------------------------
# A lazy adapter: the state, the value, and the three functions


@dataclass(frozen=True)
class LazyKind:
    """One lazy adapter: the layout of its state and the body of its `next_fn`.

    `second` is the LLVM type of the second field, or None for a state that holds the
    inner iterator alone. `second_is_held` says whether that field is a value with its own
    `drop` field (a function, an iterator) that the state destroys and clones.
    """
    name: str
    second: Optional[Callable[['LLVMCodegen'], ir.Type]]
    second_is_held: bool
    next_body: Callable[..., None]

    def state_type(self, codegen: 'LLVMCodegen') -> ir.LiteralStructType:
        fields = [codegen.types.closure_struct]
        if self.second is not None:
            fields.append(self.second(codegen))
        return ir.LiteralStructType(fields)


def _emit_lazy(codegen: 'LLVMCodegen', kind: LazyKind, fields: Sequence[ir.Value],
               types: Sequence['Type'], context: tuple, heap: bool) -> ir.Value:
    """The value of a lazy adapter: `fields` in a heap state, or in a stack state."""
    state_type = kind.state_type(codegen)
    if heap:
        raw, state = _heap_state(codegen, state_type)
    else:
        state = entry_alloca(codegen.builder, state_type, f"iter_{kind.name}_state")
        raw = state
    for index, value in enumerate(fields):
        codegen.builder.store(value, gep_utils.gep_struct_field(
            codegen, state, index, f"iter_{kind.name}_field"))
    next_fn = _lazy_next(codegen, kind, types, context)
    return iterator_value(codegen, next_fn, raw, _lazy_drop(codegen, kind, heap),
                          _lazy_clone(codegen, kind, types, context))


def _lazy_next(codegen: 'LLVMCodegen', kind: LazyKind, types: Sequence['Type'],
               context: tuple) -> ir.Function:
    result = types[-1]
    if not isinstance(result, IteratorType):
        raise_internal_error("CE0015", message="a lazy adapter answers no iterator")

    def body(fn: ir.Function) -> None:
        state = codegen.builder.bitcast(fn.args[0], ir.PointerType(kind.state_type(codegen)),
                                        name="state")
        kind.next_body(codegen, fn, state, *context)

    return _function(codegen, f"__sushi_iter_{kind.name}_next_{_key(*types)}",
                     _next_type(codegen, result.element_type), body)


def _lazy_drop(codegen: 'LLVMCodegen', kind: LazyKind, frees: bool) -> ir.Function:
    """`drop_fn`: destroy the inner iterator and the held second field; free a heap
    state."""
    from sushi_lang.backend.memory.heap import emit_free

    def body(fn: ir.Function) -> None:
        b = codegen.builder
        state = b.bitcast(fn.args[0], ir.PointerType(kind.state_type(codegen)))
        _destroy_held(codegen, b.load(_field(codegen, state, _INNER)))
        if kind.second_is_held:
            _destroy_held(codegen, b.load(_field(codegen, state, _SECOND)))
        if frees:
            emit_free(b, codegen, fn.args[0])
        b.ret_void()

    placement = "heap" if frees else "stack"
    return _function(codegen, f"__sushi_iter_{kind.name}_drop_{placement}",
                     _drop_type(codegen), body)


def _lazy_clone(codegen: 'LLVMCodegen', kind: LazyKind, types: Sequence['Type'],
                context: tuple) -> ir.Function:
    """`clone_fn`: a heap state with a clone of each held field, at the same position."""
    from sushi_lang.backend.ownership import copy_out_into

    def body(fn: ir.Function) -> None:
        b = codegen.builder
        state_type = kind.state_type(codegen)
        old = b.bitcast(fn.args[0], ir.PointerType(state_type), name="old_state")
        raw, state = _heap_state(codegen, state_type)
        inner = emit_iterator_clone(codegen, b.load(_field(codegen, old, _INNER)))
        b.store(inner, _field(codegen, state, _INNER))
        if kind.second is not None:
            dest, source = _field(codegen, state, _SECOND), _field(codegen, old, _SECOND)
            if kind.second_is_held and kind.name != "zip":
                copy_out_into(codegen, dest, source, context[0])
            else:
                second = b.load(source)
                if kind.second_is_held:
                    second = emit_iterator_clone(codegen, second)
                b.store(second, dest)
        b.ret(iterator_value(codegen, _lazy_next(codegen, kind, types, context), raw,
                             _lazy_drop(codegen, kind, True), fn))

    return _function(codegen, f"__sushi_iter_{kind.name}_clone_{_key(*types)}",
                     _clone_type(codegen), body)


def _field(codegen: 'LLVMCodegen', state: ir.Value, index: int) -> ir.Value:
    return gep_utils.gep_struct_field(codegen, state, index, "iter_field")


def _blocks(fn: ir.Function, *names: str) -> list[ir.Block]:
    return [fn.append_basic_block(name) for name in names]


# ---------------------------------------------------------------------------------------
# The five `next_fn` bodies. Each is emitted with `codegen.builder` at the entry block.


def _map_next(codegen: 'LLVMCodegen', fn: ir.Function, state: ir.Value,
              fn_type: FunctionType, source: IteratorType, result: IteratorType) -> None:
    """The next item of the source, given to the function; the item is then destroyed in
    the parts it owns, and the function's answer is the item of the result."""
    from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some
    b = codegen.builder
    found, done = _blocks(fn, "map.found", "map.done")
    item = _next_item(codegen, b.load(_field(codegen, state, _INNER)), source.element_type,
                      found, done)
    b.position_at_end(found)
    value = item()
    mapped = _call(codegen, b.load(_field(codegen, state, _SECOND)), fn_type, [value])
    destroy_owned_parts(codegen, value, source)
    b.ret(emit_maybe_some(codegen, result.element_type, mapped))
    b.position_at_end(done)
    b.ret(emit_maybe_none(codegen, result.element_type))


def _filter_next(codegen: 'LLVMCodegen', fn: ir.Function, state: ir.Value,
                 fn_type: FunctionType, source: IteratorType, result: IteratorType) -> None:
    """The next item for which the predicate answers true. An item that the predicate
    refuses is destroyed in the parts it owns."""
    from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some
    b = codegen.builder
    step, found, keep, drop, done = _blocks(fn, "filter.step", "filter.found", "filter.keep",
                                            "filter.drop", "filter.done")
    b.branch(step)
    b.position_at_end(step)
    item = _next_item(codegen, b.load(_field(codegen, state, _INNER)), source.element_type,
                      found, done)
    b.position_at_end(found)
    value = item()
    answer = _call(codegen, b.load(_field(codegen, state, _SECOND)), fn_type, [value])
    b.cbranch(codegen.utils.as_i1(answer), keep, drop)
    b.position_at_end(keep)
    b.ret(emit_maybe_some(codegen, result.element_type, value))
    b.position_at_end(drop)
    destroy_owned_parts(codegen, value, source)
    b.branch(step)
    b.position_at_end(done)
    b.ret(emit_maybe_none(codegen, result.element_type))


def _enumerate_next(codegen: 'LLVMCodegen', fn: ir.Function, state: ir.Value,
                    source: IteratorType, result: IteratorType) -> None:
    """`(count, item)`, the count from 0."""
    from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some
    b = codegen.builder
    found, done = _blocks(fn, "enumerate.found", "enumerate.done")
    item = _next_item(codegen, b.load(_field(codegen, state, _INNER)), source.element_type,
                      found, done)
    b.position_at_end(found)
    count_ptr = _field(codegen, state, _SECOND)
    count = b.load(count_ptr, name="count")
    b.store(b.add(count, ir.Constant(codegen.types.i32, 1)), count_ptr)
    pair = _tuple(codegen, result.element_type, (count, item()))
    b.ret(emit_maybe_some(codegen, result.element_type, pair))
    b.position_at_end(done)
    b.ret(emit_maybe_none(codegen, result.element_type))


def _zip_next(codegen: 'LLVMCodegen', fn: ir.Function, state: ir.Value,
              left: IteratorType, right: IteratorType, result: IteratorType) -> None:
    """`(left item, right item)`, until one side ends. A left item with no right item is
    destroyed in the parts it owns."""
    from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some
    b = codegen.builder
    left_found, both, right_done, done = _blocks(fn, "zip.left", "zip.both", "zip.right_done",
                                                 "zip.done")
    left_item = _next_item(codegen, b.load(_field(codegen, state, _INNER)),
                           left.element_type, left_found, done)
    b.position_at_end(left_found)
    first = left_item()
    right_item = _next_item(codegen, b.load(_field(codegen, state, _SECOND)),
                            right.element_type, both, right_done)
    b.position_at_end(right_done)
    destroy_owned_parts(codegen, first, left)
    b.branch(done)
    b.position_at_end(both)
    pair = _tuple(codegen, result.element_type, (first, right_item()))
    b.ret(emit_maybe_some(codegen, result.element_type, pair))
    b.position_at_end(done)
    b.ret(emit_maybe_none(codegen, result.element_type))


def _clone_each_next(codegen: 'LLVMCodegen', fn: ir.Function, state: ir.Value,
                     source: IteratorType, result: IteratorType) -> None:
    """The next item with each lent part replaced by its clone (`copy_out`)."""
    from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some
    from sushi_lang.backend.ownership import copy_out
    b = codegen.builder
    found, done = _blocks(fn, "clone_each.found", "clone_each.done")
    item = _next_item(codegen, b.load(_field(codegen, state, _INNER)), source.element_type,
                      found, done)
    b.position_at_end(found)
    value = item()
    if len(source.peeks) == 1:
        value = copy_out(codegen, value, source.element_type)
    elif source.peeks:
        for index, (part, lent) in enumerate(zip(item_parts(source), source.peeks,
                                                 strict=True)):
            if lent:
                copy = copy_out(codegen, b.extract_value(value, index), part)
                value = b.insert_value(value, copy, index)
    b.ret(emit_maybe_some(codegen, result.element_type, value))
    b.position_at_end(done)
    b.ret(emit_maybe_none(codegen, result.element_type))


def _closure_field(codegen: 'LLVMCodegen') -> ir.Type:
    return codegen.types.closure_struct


def _count_field(codegen: 'LLVMCodegen') -> ir.Type:
    return codegen.types.i32


_MAP = LazyKind("map", _closure_field, True, _map_next)
_FILTER = LazyKind("filter", _closure_field, True, _filter_next)
_ENUMERATE = LazyKind("enumerate", _count_field, False, _enumerate_next)
_ZIP = LazyKind("zip", _closure_field, True, _zip_next)
_CLONE_EACH = LazyKind("clone_each", None, False, _clone_each_next)


def emit_map(codegen: 'LLVMCodegen', inner: ir.Value, function: ir.Value,
             fn_type: FunctionType, source: IteratorType, result: IteratorType,
             heap: bool) -> ir.Value:
    return _emit_lazy(codegen, _MAP, (inner, function), (source, result),
                      (fn_type, source, result), heap)


def emit_filter(codegen: 'LLVMCodegen', inner: ir.Value, predicate: ir.Value,
                fn_type: FunctionType, source: IteratorType, result: IteratorType,
                heap: bool) -> ir.Value:
    return _emit_lazy(codegen, _FILTER, (inner, predicate), (source,),
                      (fn_type, source, result), heap)


def emit_enumerate(codegen: 'LLVMCodegen', inner: ir.Value, source: IteratorType,
                   result: IteratorType, heap: bool) -> ir.Value:
    return _emit_lazy(codegen, _ENUMERATE, (inner, ir.Constant(codegen.types.i32, 0)),
                      (source, result), (source, result), heap)


def emit_zip(codegen: 'LLVMCodegen', inner: ir.Value, other: ir.Value, source: IteratorType,
             right: IteratorType, result: IteratorType, heap: bool) -> ir.Value:
    return _emit_lazy(codegen, _ZIP, (inner, other), (source, right, result),
                      (source, right, result), heap)


def emit_clone_each(codegen: 'LLVMCodegen', inner: ir.Value, source: IteratorType,
                    result: IteratorType, heap: bool) -> ir.Value:
    return _emit_lazy(codegen, _CLONE_EACH, (inner,), (source, result), (source, result),
                      heap)


# ---------------------------------------------------------------------------------------
# The consumers: a walk to the end, in the caller's function


def _walk(codegen: 'LLVMCodegen', value: ir.Value, receiver: IteratorType,
          step: Callable[[ir.Value], None]) -> None:
    """Call `step(item)` for each item of the iterator `value`, then destroy the iterator.

    The consumer took the iterator, so it is destroyed here, after the last item.
    """
    from sushi_lang.backend.destructors import emit_function_value_destructor
    b = codegen.builder
    slot = entry_alloca(codegen.builder, value.type, "iter_walked")
    b.store(value, slot)
    loop, found, done = _blocks(codegen.func, "consume.next", "consume.item", "consume.end")
    b.branch(loop)
    b.position_at_end(loop)
    item = _next_item(codegen, b.load(slot), receiver.element_type, found, done)
    b.position_at_end(found)
    step(item())
    b.branch(loop)
    b.position_at_end(done)
    emit_function_value_destructor(codegen, slot)


def _new_list(codegen: 'LLVMCodegen', list_type: 'Type') -> ir.Value:
    from sushi_lang.backend.generics.list.methods_simple import emit_list_new
    if not isinstance(list_type, StructType):
        raise_internal_error("CE0015", message="a consumer reached the backend with no "
                                               "List type")
    slot = entry_alloca(codegen.builder, codegen.types.ll_type(list_type), "collected")
    codegen.builder.store(emit_list_new(codegen, list_type), slot)
    return slot


def _append(codegen: 'LLVMCodegen', list_slot: ir.Value, item: ir.Value) -> None:
    from sushi_lang.backend.generics.list.methods_modify import emit_list_append
    emit_list_append(codegen, list_slot, item)


def emit_collect(codegen: 'LLVMCodegen', iterator: ir.Value, receiver: IteratorType,
                 list_type: 'Type') -> ir.Value:
    """Each item into a new `List@(T)`. The items are owned, or plain and copied."""
    collected = _new_list(codegen, list_type)
    _walk(codegen, iterator, receiver, lambda item: _append(codegen, collected, item))
    return codegen.builder.load(collected, name="collected_list")


def emit_partition(codegen: 'LLVMCodegen', iterator: ir.Value, receiver: IteratorType,
                   pred: ir.Value, fn_type: FunctionType, pair_type: 'Type') -> ir.Value:
    """Each item into the first list when the predicate answers true, else the second."""
    from sushi_lang.semantics.generics.tuples import tuple_elements
    list_type = tuple_elements(pair_type)[0]
    kept, rest = _new_list(codegen, list_type), _new_list(codegen, list_type)

    def step(item: ir.Value) -> None:
        b = codegen.builder
        answer = codegen.utils.as_i1(_call(codegen, pred, fn_type, [item]))
        yes, no, joined = _blocks(codegen.func, "partition.kept", "partition.rest",
                                  "partition.join")
        b.cbranch(answer, yes, no)
        b.position_at_end(yes)
        _append(codegen, kept, item)
        b.branch(joined)
        b.position_at_end(no)
        _append(codegen, rest, item)
        b.branch(joined)
        b.position_at_end(joined)

    _walk(codegen, iterator, receiver, step)
    _destroy_held(codegen, pred)
    b = codegen.builder
    return _tuple(codegen, pair_type, (b.load(kept), b.load(rest)))


def emit_fold(codegen: 'LLVMCodegen', iterator: ir.Value, receiver: IteratorType,
              init: ir.Value, step_fn: ir.Value, fn_type: FunctionType,
              result_type: 'Type') -> ir.Value:
    """The accumulator through the function, item by item. Each item is given as a
    borrow, and each replaced accumulator is destroyed."""
    b = codegen.builder
    acc = entry_alloca(codegen.builder, init.type, "fold_acc")
    b.store(init, acc)

    def step(item: ir.Value) -> None:
        old = b.load(acc, name="acc")
        new = _call(codegen, step_fn, fn_type, [old, item])
        _destroy_value(codegen, old, result_type)
        b.store(new, acc)
        destroy_owned_parts(codegen, item, receiver)

    _walk(codegen, iterator, receiver, step)
    _destroy_held(codegen, step_fn)
    return b.load(acc, name="folded")
