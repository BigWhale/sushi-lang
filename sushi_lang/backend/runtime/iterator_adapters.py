"""The adapters of `Iterator@(T)` (#1228, docs/design/iteration.md).

A LAZY adapter (`map`, `filter`, `enumerate`, `zip`, `clone_each`) answers a new iterator
value: its state holds what the adapter keeps (the iterator it reads, the function it
calls, a count), and its `next_fn` asks the inner iterator for an item and changes it. A
CONSUMER (`fold`, `partition`, `collect`) walks its receiver to the end at the call, in
the caller's function.

Every adapter takes its receiver and its arguments. The state of an adapter over a LENT
iterator is a stack slot of the caller (an `entry_alloca`), as the state of its input is,
and its `drop_fn` destroys what the state holds and frees nothing. The state of an adapter
over an OWNED iterator is on the heap, and its `drop_fn` destroys what it holds and frees
the block. The borrow pass chose (`MethodCall.iterator_state`).

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
from sushi_lang.semantics.generics.iterators import LENT, OWNED, item_parts
from sushi_lang.semantics.typesys import FunctionType, IteratorType, StructType

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
# The call


def emit_adapter_call(codegen: 'LLVMCodegen', expr, receiver_value: ir.Value,
                      receiver_type: IteratorType) -> ir.Value:
    """Emit `it.<adapter>(args)`: take the receiver and the arguments, then make the
    value (a lazy adapter) or walk the receiver to its end (a consumer)."""
    from sushi_lang.backend.expressions.calls.dispatcher import (
        consume_receiver, emit_method_call_arguments, settle_method_call_arguments)

    args = emit_method_call_arguments(codegen, expr)
    value = consume_receiver(codegen, expr, receiver_value)
    settle_method_call_arguments(codegen, expr, args)
    emitter = _EMITTERS.get(expr.method)
    if emitter is None:
        raise_internal_error("CE0015", message=f"no emitter for the iterator adapter "
                                               f"`.{expr.method}()`")
    return emitter(codegen, AdapterCall(expr, value, receiver_type, tuple(args)))


@dataclass(frozen=True)
class AdapterCall:
    """One adapter call after its receiver and its arguments were taken."""
    expr: object
    value: ir.Value
    receiver: IteratorType
    args: tuple

    @property
    def result(self) -> Optional['Type']:
        """What the call answers, as the typecheck pass stamped it."""
        return getattr(self.expr, "inferred_return_type", None)

    def result_type(self) -> 'Type':
        result = self.result
        if result is None:
            raise_internal_error("CE0015", message="an iterator adapter reached the backend "
                                                   "untyped")
        return result

    def result_iterator(self) -> IteratorType:
        result = self.result
        if not isinstance(result, IteratorType):
            raise_internal_error("CE0015", message="an iterator adapter reached the backend "
                                                   "with no iterator type")
        return result

    def param(self, index: int) -> Optional['Type']:
        """The type of parameter `index`, as the solved signature states it."""
        types = tuple(getattr(self.expr, "callee_param_types", None) or ())
        return types[index] if index < len(types) else None

    def function_type(self, index: int) -> FunctionType:
        fn_type = self.param(index)
        if not isinstance(fn_type, FunctionType):
            raise_internal_error("CE0015", message="an iterator adapter reached the backend "
                                                   "with no function type")
        return fn_type


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


def owned_parts(iterator_type: IteratorType) -> tuple[tuple[int, 'Type'], ...]:
    """The owned parts of a tuple item whose parts differ in mode: `(index, type)`."""
    if len(iterator_type.peeks) <= 1:
        return ()
    return tuple((index, part) for index, (part, lent) in enumerate(
        zip(item_parts(iterator_type), iterator_type.peeks, strict=True)) if not lent)


def _destroy_value(codegen: 'LLVMCodegen', value: ir.Value, value_type: 'Type') -> None:
    from sushi_lang.backend.destructors import emit_value_destructor, needs_cleanup
    if not needs_cleanup(codegen, value_type):
        return
    slot = codegen.memory.entry_alloca(value.type, "iter_item_drop")
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


def _emit_lazy(codegen: 'LLVMCodegen', kind: LazyKind, expr, fields: Sequence[ir.Value],
               types: Sequence['Type'], context: tuple) -> ir.Value:
    """The value of a lazy adapter: `fields` in a stack or a heap state."""
    state_type = kind.state_type(codegen)
    placement = getattr(expr, "iterator_state", None)
    if placement == LENT:
        state = codegen.memory.entry_alloca(state_type, f"iter_{kind.name}_state")
        raw = state
    elif placement == OWNED:
        raw, state = _heap_state(codegen, state_type)
    else:
        raise_internal_error("CE0015", message=f"the adapter `.{expr.method}()` reached "
                                               f"the backend with no state")
    for index, value in enumerate(fields):
        codegen.builder.store(value, gep_utils.gep_struct_field(
            codegen, state, index, f"iter_{kind.name}_field"))
    next_fn = _lazy_next(codegen, kind, types, context)
    return iterator_value(codegen, next_fn, raw, _lazy_drop(codegen, kind, placement == OWNED),
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
    from sushi_lang.backend.ownership import copy_out

    def body(fn: ir.Function) -> None:
        b = codegen.builder
        state_type = kind.state_type(codegen)
        old = b.bitcast(fn.args[0], ir.PointerType(state_type), name="old_state")
        raw, state = _heap_state(codegen, state_type)
        inner = emit_iterator_clone(codegen, b.load(_field(codegen, old, _INNER)))
        b.store(inner, _field(codegen, state, _INNER))
        if kind.second is not None:
            second = b.load(_field(codegen, old, _SECOND))
            if kind.second_is_held:
                second = (emit_iterator_clone(codegen, second) if kind.name == "zip"
                          else copy_out(codegen, second, context[0]))
            b.store(second, _field(codegen, state, _SECOND))
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


def _emit_map(codegen: 'LLVMCodegen', call: AdapterCall) -> ir.Value:
    result = call.result_iterator()
    return _emit_lazy(codegen, _MAP, call.expr, (call.value, call.args[0]),
                      (call.receiver, result), (call.function_type(0), call.receiver, result))


def _emit_filter(codegen: 'LLVMCodegen', call: AdapterCall) -> ir.Value:
    result = call.result_iterator()
    return _emit_lazy(codegen, _FILTER, call.expr, (call.value, call.args[0]),
                      (call.receiver,), (call.function_type(0), call.receiver, result))


def _emit_enumerate(codegen: 'LLVMCodegen', call: AdapterCall) -> ir.Value:
    result = call.result_iterator()
    return _emit_lazy(codegen, _ENUMERATE, call.expr,
                      (call.value, ir.Constant(codegen.types.i32, 0)),
                      (call.receiver, result), (call.receiver, result))


def _emit_zip(codegen: 'LLVMCodegen', call: AdapterCall) -> ir.Value:
    right = call.param(0)
    if not isinstance(right, IteratorType):
        raise_internal_error("CE0015", message="`zip` reached the backend with no "
                                               "iterator argument type")
    result = call.result_iterator()
    return _emit_lazy(codegen, _ZIP, call.expr, (call.value, call.args[0]),
                      (call.receiver, right, result), (call.receiver, right, result))


def _emit_clone_each(codegen: 'LLVMCodegen', call: AdapterCall) -> ir.Value:
    result = call.result_iterator()
    return _emit_lazy(codegen, _CLONE_EACH, call.expr, (call.value,),
                      (call.receiver, result), (call.receiver, result))


# ---------------------------------------------------------------------------------------
# The consumers: a walk to the end, in the caller's function


def _walk(codegen: 'LLVMCodegen', value: ir.Value, receiver: IteratorType,
          step: Callable[[ir.Value], None]) -> None:
    """Call `step(item)` for each item of the iterator `value`, then destroy the iterator.

    The consumer took the iterator, so it is destroyed here, after the last item.
    """
    from sushi_lang.backend.destructors import emit_function_value_destructor
    b = codegen.builder
    slot = codegen.memory.entry_alloca(value.type, "iter_walked")
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
    slot = codegen.memory.entry_alloca(codegen.types.ll_type(list_type), "collected")
    codegen.builder.store(emit_list_new(codegen, list_type), slot)
    return slot


def _append(codegen: 'LLVMCodegen', list_slot: ir.Value, item: ir.Value) -> None:
    from sushi_lang.backend.generics.list.methods_modify import emit_list_append
    emit_list_append(codegen, list_slot, lambda: item)


def _emit_collect(codegen: 'LLVMCodegen', call: AdapterCall) -> ir.Value:
    """Each item into a new `List@(T)`. The items are owned, or plain and copied."""
    collected = _new_list(codegen, call.result_type())
    _walk(codegen, call.value, call.receiver,
          lambda item: _append(codegen, collected, item))
    return codegen.builder.load(collected, name="collected_list")


def _emit_partition(codegen: 'LLVMCodegen', call: AdapterCall) -> ir.Value:
    """Each item into the first list when the predicate answers true, else the second."""
    from sushi_lang.semantics.generics.tuples import tuple_elements
    fn_type = call.function_type(0)
    pair_type = call.result_type()
    list_type = tuple_elements(pair_type)[0]
    kept, rest = _new_list(codegen, list_type), _new_list(codegen, list_type)
    pred = call.args[0]

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

    _walk(codegen, call.value, call.receiver, step)
    _destroy_held(codegen, pred)
    b = codegen.builder
    return _tuple(codegen, pair_type, (b.load(kept), b.load(rest)))


def _emit_fold(codegen: 'LLVMCodegen', call: AdapterCall) -> ir.Value:
    """The accumulator through the function, item by item. Each item is given as a
    borrow, and each replaced accumulator is destroyed."""
    fn_type = call.function_type(1)
    init, step_fn = call.args
    b = codegen.builder
    acc = codegen.memory.entry_alloca(init.type, "fold_acc")
    b.store(init, acc)

    def step(item: ir.Value) -> None:
        old = b.load(acc, name="acc")
        new = _call(codegen, step_fn, fn_type, [old, item])
        _destroy_value(codegen, old, call.result_type())
        b.store(new, acc)
        destroy_owned_parts(codegen, item, call.receiver)

    _walk(codegen, call.value, call.receiver, step)
    _destroy_held(codegen, step_fn)
    return b.load(acc, name="folded")


_EMITTERS = {
    "map": _emit_map, "filter": _emit_filter, "enumerate": _emit_enumerate, "zip": _emit_zip,
    "clone_each": _emit_clone_each, "collect": _emit_collect, "partition": _emit_partition,
    "fold": _emit_fold,
}
