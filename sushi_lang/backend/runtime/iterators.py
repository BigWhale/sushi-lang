"""The `Iterator@(T)` value: `{next_fn, state, drop_fn, clone_fn}` (#1259).

The value has the four fields of a closure, but its own calling convention, and the
backend reads the SEMANTIC type (`IteratorType`) to know it is one:

- `next_fn(i8* state) -> Maybe@(T)` answers the next item, or `None` at the end.
- `drop_fn(i8* state)` frees what the state owns. Null when the state owns nothing.
- `clone_fn(i8* state) -> {next_fn, state, drop_fn, clone_fn}` answers a whole new value:
  the copy of the cursor, on the heap, with the drop function of a heap state.

Each source supplies its own three functions: a cursor over contiguous elements (`T[]`,
`T[N]`, `List@(T)`), a cursor over the buckets of a HashMap (one per method), a range,
and `Iterator.new`. The state of a LENT iterator is a stack slot of the caller (an
`entry_alloca`), so its `drop_fn` is null; an OWNED iterator keeps its state on the heap,
and its `drop_fn` destroys what the state holds and frees it.

A function here is emitted once per module, out of line, with `codegen.builder` and
`codegen.func` swapped for its own, as the lifecycle bodies are (`backend/lifecycle.py`).
The design is docs/design/iteration.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from llvmlite import ir

from sushi_lang.backend import gep_utils
from sushi_lang.backend.lifecycle import lifecycle_symbol
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.memory.heap import emit_free, emit_malloc
from sushi_lang.backend.runtime.closures import build_closure_value
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.iterators import CURSOR, LENT, OWNED, state_on_heap

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type

#: The name of the `next_fn` pointer at a walk of a value; a fixture reads it in the IR.
NEXT_FN_NAME = "iter_next_fn"

# The fields of a cursor, `{i32 index, i32 length, E* data}`.
_INDEX, _LENGTH, _DATA = 0, 1, 2
# The fields of an owned state, `{cursor, container}`.
_CURSOR, _CONTAINER = 0, 1


# ---------------------------------------------------------------------------------------
# The value and its three function types


def iterator_value(codegen: 'LLVMCodegen', next_fn: ir.Function, state: ir.Value,
                   drop_fn: Optional[ir.Function],
                   clone_fn: Optional[ir.Function]) -> ir.Value:
    """Assemble an iterator value at `codegen.builder`."""
    b = codegen.builder
    i8p = codegen.types.str_ptr

    def erase(value: Optional[ir.Value]) -> ir.Value:
        if value is None:
            return ir.Constant(i8p, None)
        return value if value.type == i8p else b.bitcast(value, i8p)

    return build_closure_value(codegen, erase(next_fn), erase(state), erase(drop_fn),
                               erase(clone_fn))


def _next_type(codegen: 'LLVMCodegen', item_type: 'Type') -> ir.FunctionType:
    from sushi_lang.backend.generics.maybe import get_maybe_enum_type
    return ir.FunctionType(get_maybe_enum_type(codegen, item_type), [codegen.types.str_ptr])


def _drop_type(codegen: 'LLVMCodegen') -> ir.FunctionType:
    return ir.FunctionType(ir.VoidType(), [codegen.types.str_ptr])


def _clone_type(codegen: 'LLVMCodegen') -> ir.FunctionType:
    return ir.FunctionType(codegen.types.closure_struct, [codegen.types.str_ptr])


def _function(codegen: 'LLVMCodegen', name: str, fn_type: ir.FunctionType,
              emit_body: Callable[[ir.Function], None]) -> ir.Function:
    """The function `name` of this module, emitted on first use by `emit_body`."""
    existing = codegen.module.globals.get(name)
    if isinstance(existing, ir.Function):
        return existing
    fn = ir.Function(codegen.module, fn_type, name=name)
    fn.linkage = "linkonce_odr"
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    saved = codegen.builder, codegen.func
    codegen.builder, codegen.func = builder, fn
    try:
        emit_body(fn)
    finally:
        codegen.builder, codegen.func = saved
    return fn


def _heap_state(codegen: 'LLVMCodegen', state_type: ir.Type) -> tuple[ir.Value, ir.Value]:
    """A new heap state of `state_type`: the `i8*` and the typed pointer."""
    from sushi_lang.backend.expressions.memory import get_element_size_constant
    size = get_element_size_constant(codegen, state_type)
    raw = emit_malloc(codegen, codegen.builder, size)
    return raw, codegen.builder.bitcast(raw, ir.PointerType(state_type), name="iter_state")


def _free_state(codegen: 'LLVMCodegen') -> ir.Function:
    """`drop_fn` of a state that owns nothing but its own heap block."""
    def body(fn: ir.Function) -> None:
        emit_free(codegen.builder, codegen, fn.args[0])
        codegen.builder.ret_void()
    return _function(codegen, "__sushi_iter_free_state", _drop_type(codegen), body)


# ---------------------------------------------------------------------------------------
# Walking a value


def emit_next(codegen: 'LLVMCodegen', value: ir.Value, item_type: 'Type') -> ir.Value:
    """Call `next_fn(state)` of an iterator value; answers its `Maybe@(T)`."""
    b = codegen.builder
    fn_type = _next_type(codegen, item_type)
    next_ptr = b.bitcast(b.extract_value(value, 0), ir.PointerType(fn_type),
                         name=NEXT_FN_NAME)
    return b.call(next_ptr, [b.extract_value(value, 1, name="iter_state")],
                  name="iter_answer")


def emit_iterator_clone(codegen: 'LLVMCodegen', value: ir.Value) -> ir.Value:
    """`it.clone()`: call `clone_fn(state)`, which answers the whole new value."""
    b = codegen.builder
    clone_ptr = b.bitcast(b.extract_value(value, 3), ir.PointerType(_clone_type(codegen)),
                          name="iter_clone_fn")
    return b.call(clone_ptr, [b.extract_value(value, 1)], name="iter_clone")


# ---------------------------------------------------------------------------------------
# A cursor over a source with a count of slots: an array, a List, the HashMap buckets


@dataclass(frozen=True)
class CursorSource:
    """What a cursor walks: its item and how one slot answers it.

    `key` names the source in a symbol. `item` loads the item of slot `index` of `data`;
    `live`, when there is one, answers whether a slot holds an item at all (a HashMap
    bucket may be empty), and a dead slot is stepped over.
    """
    key: str
    item_type: 'Type'
    data_type: ir.Type
    item: Callable[['LLVMCodegen', ir.Value, ir.Value], ir.Value]
    live: Optional[Callable[['LLVMCodegen', ir.Value, ir.Value], ir.Value]] = None

    def cursor_type(self, codegen: 'LLVMCodegen') -> ir.LiteralStructType:
        return ir.LiteralStructType([codegen.types.i32, codegen.types.i32,
                                     ir.PointerType(self.data_type)])


@dataclass(frozen=True)
class OwnedContainer:
    """The container an OWNED cursor takes: its value, its type, and where its slots are.

    `slots(codegen, container_ptr)` answers `(length, data)` for a container at an address.
    """
    value: ir.Value
    semantic_type: 'Type'
    slots: Callable[['LLVMCodegen', ir.Value], tuple[ir.Value, ir.Value]]


def emit_cursor_iterator(codegen: 'LLVMCodegen', call, source: CursorSource,
                         length: ir.Value, data: ir.Value,
                         owned: Optional[Callable[[], OwnedContainer]] = None) -> ir.Value:
    """What a producer call answers, by the state the borrow pass chose for it.

    CURSOR: the bare cursor, for a direct walk. LENT: a value over a stack cursor, or over a
    heap cursor when it leaves through a `peek` return (#1229). OWNED: a value that takes
    the container (`owned()` takes the receiver and gives it) and keeps it with the cursor
    on the heap.
    """
    b = codegen.builder
    cursor_type = source.cursor_type(codegen)
    state = getattr(call, "iterator_state", None)
    if state == CURSOR:
        cursor = ir.Constant(cursor_type, ir.Undefined)
        cursor = b.insert_value(cursor, ir.Constant(codegen.types.i32, 0), _INDEX)
        cursor = b.insert_value(cursor, length, _LENGTH)
        return b.insert_value(cursor, b.bitcast(data, ir.PointerType(source.data_type)),
                              _DATA, name="cursor")
    if state == LENT and state_on_heap(call):
        raw, cursor = _heap_state(codegen, cursor_type)
        _store_cursor(codegen, cursor, ir.Constant(codegen.types.i32, 0), length, data,
                      source)
        return iterator_value(codegen, _cursor_next(codegen, source), raw,
                              _free_state(codegen), _lent_cursor_clone(codegen, source))
    if state == LENT:
        slot = entry_alloca(b, cursor_type, name="iter_cursor")
        _store_cursor(codegen, slot, ir.Constant(codegen.types.i32, 0), length, data,
                      source)
        return iterator_value(codegen, _cursor_next(codegen, source), slot, None,
                              _lent_cursor_clone(codegen, source))
    if state == OWNED and owned is not None:
        return _owned_cursor_iterator(codegen, source, owned())
    raise_internal_error("CE0015", message=f"an iterator producer `.{call.method}()` "
                                           f"reached the backend with no state")


def _store_cursor(codegen: 'LLVMCodegen', cursor_ptr: ir.Value, index: ir.Value,
                  length: ir.Value, data: ir.Value, source: CursorSource) -> None:
    b = codegen.builder
    b.store(index, gep_utils.gep_struct_field(codegen, cursor_ptr, _INDEX, "iter_index"))
    b.store(length, gep_utils.gep_struct_field(codegen, cursor_ptr, _LENGTH, "iter_length"))
    b.store(b.bitcast(data, ir.PointerType(source.data_type)),
            gep_utils.gep_struct_field(codegen, cursor_ptr, _DATA, "iter_data"))


def _cursor_next(codegen: 'LLVMCodegen', source: CursorSource) -> ir.Function:
    """`next_fn` of a cursor: the item of the next live slot, or `None` after the last."""
    from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some

    def body(fn: ir.Function) -> None:
        b = codegen.builder
        cursor = b.bitcast(fn.args[0], ir.PointerType(source.cursor_type(codegen)),
                           name="cursor")
        step = fn.append_basic_block("next.step")
        slot = fn.append_basic_block("next.slot")
        found = fn.append_basic_block("next.found")
        done = fn.append_basic_block("next.done")
        b.branch(step)

        b.position_at_end(step)
        index_ptr = gep_utils.gep_struct_field(codegen, cursor, _INDEX, "index_ptr")
        index = b.load(index_ptr, name="index")
        length = b.load(gep_utils.gep_struct_field(codegen, cursor, _LENGTH, "length_ptr"),
                        name="length")
        b.cbranch(b.icmp_signed("<", index, length, name="more"), slot, done)

        b.position_at_end(slot)
        data = b.load(gep_utils.gep_struct_field(codegen, cursor, _DATA, "data_ptr"),
                      name="data")
        b.store(b.add(index, ir.Constant(codegen.types.i32, 1), name="next_index"),
                index_ptr)
        if source.live is None:
            b.branch(found)
        else:
            b.cbranch(source.live(codegen, data, index), found, step)

        b.position_at_end(found)
        b.ret(emit_maybe_some(codegen, source.item_type, source.item(codegen, data, index)))

        b.position_at_end(done)
        b.ret(emit_maybe_none(codegen, source.item_type))

    return _function(codegen, f"__sushi_iter_next_{source.key}",
                     _next_type(codegen, source.item_type), body)


def _lent_cursor_clone(codegen: 'LLVMCodegen', source: CursorSource) -> ir.Function:
    """`clone_fn` of a lent cursor: the same position, on the heap, over the same slots.

    The clone borrows the same owners as the original, which the borrow pass states. Its
    state is a heap copy of the cursor, so its `drop_fn` frees that copy and nothing else.
    """
    def body(fn: ir.Function) -> None:
        b = codegen.builder
        cursor_type = source.cursor_type(codegen)
        raw, copy = _heap_state(codegen, cursor_type)
        old = b.bitcast(fn.args[0], ir.PointerType(cursor_type))
        b.store(b.load(old, name="cursor"), copy)
        b.ret(iterator_value(codegen, _cursor_next(codegen, source), raw,
                             _free_state(codegen), fn))

    return _function(codegen, f"__sushi_iter_clone_{source.key}", _clone_type(codegen),
                     body)


def _owned_state_type(codegen: 'LLVMCodegen', source: CursorSource,
                      container_type: 'Type') -> ir.LiteralStructType:
    return ir.LiteralStructType([source.cursor_type(codegen),
                                 codegen.types.ll_type(container_type)])


def _owned_cursor_iterator(codegen: 'LLVMCodegen', source: CursorSource,
                           container: OwnedContainer) -> ir.Value:
    """A cursor that owns its container: both on the heap, the container after the cursor."""
    b = codegen.builder
    state_type = _owned_state_type(codegen, source, container.semantic_type)
    raw, state = _heap_state(codegen, state_type)
    held = gep_utils.gep_struct_field(codegen, state, _CONTAINER, "iter_container")
    b.store(container.value, held)
    length, data = container.slots(codegen, held)
    cursor = gep_utils.gep_struct_field(codegen, state, _CURSOR, "iter_cursor")
    _store_cursor(codegen, cursor, ir.Constant(codegen.types.i32, 0), length, data, source)
    return iterator_value(codegen, _cursor_next(codegen, source), raw,
                          _owned_drop(codegen, source, container),
                          _owned_clone(codegen, source, container))


def _owned_key(source: CursorSource, container: OwnedContainer) -> str:
    return lifecycle_symbol(f"{source.key}_", container.semantic_type)


def _owned_drop(codegen: 'LLVMCodegen', source: CursorSource,
                container: OwnedContainer) -> ir.Function:
    """`drop_fn` of an owned cursor: destroy the container, then free the state."""
    from sushi_lang.backend.destructors import emit_value_destructor

    def body(fn: ir.Function) -> None:
        b = codegen.builder
        state = b.bitcast(fn.args[0], ir.PointerType(
            _owned_state_type(codegen, source, container.semantic_type)))
        held = gep_utils.gep_struct_field(codegen, state, _CONTAINER, "iter_container")
        emit_value_destructor(codegen, held, container.semantic_type)
        emit_free(b, codegen, fn.args[0])
        b.ret_void()

    return _function(codegen, f"__sushi_iter_drop_{_owned_key(source, container)}",
                     _drop_type(codegen), body)


def _owned_clone(codegen: 'LLVMCodegen', source: CursorSource,
                 container: OwnedContainer) -> ir.Function:
    """`clone_fn` of an owned cursor: a deep copy of the container, at the same position."""
    from sushi_lang.backend.ownership import copy_out

    def body(fn: ir.Function) -> None:
        b = codegen.builder
        state_type = _owned_state_type(codegen, source, container.semantic_type)
        old = b.bitcast(fn.args[0], ir.PointerType(state_type), name="old_state")
        raw, state = _heap_state(codegen, state_type)
        old_held = gep_utils.gep_struct_field(codegen, old, _CONTAINER, "old_container")
        held = gep_utils.gep_struct_field(codegen, state, _CONTAINER, "iter_container")
        b.store(copy_out(codegen, old_held, container.semantic_type), held)
        old_cursor = gep_utils.gep_struct_field(codegen, old, _CURSOR, "old_cursor")
        index = b.load(gep_utils.gep_struct_field(codegen, old_cursor, _INDEX, "old_index"),
                       name="index")
        length, data = container.slots(codegen, held)
        cursor = gep_utils.gep_struct_field(codegen, state, _CURSOR, "iter_cursor")
        _store_cursor(codegen, cursor, index, length, data, source)
        b.ret(iterator_value(codegen, _cursor_next(codegen, source), raw,
                             _owned_drop(codegen, source, container), fn))

    return _function(codegen, f"__sushi_iter_clone_{_owned_key(source, container)}",
                     _clone_type(codegen), body)


def array_source(codegen: 'LLVMCodegen', element_type: 'Type') -> CursorSource:
    """The cursor over contiguous elements of `element_type`: an array or a List."""
    def item(codegen: 'LLVMCodegen', data: ir.Value, index: ir.Value) -> ir.Value:
        b = codegen.builder
        return b.load(b.gep(data, [index], name="item_ptr"), name="item")
    return CursorSource(key=lifecycle_symbol("elements_", element_type),
                        item_type=element_type,
                        data_type=codegen.types.ll_type(element_type), item=item)


# ---------------------------------------------------------------------------------------
# A range and `Iterator.new`: an owned state on the heap

# The range state: `{i32 k, i32 count, i32 first, i32 step}`; the item is first + step * k.
_RANGE_K, _RANGE_COUNT, _RANGE_FIRST, _RANGE_STEP = 0, 1, 2, 3


def emit_range_iterator(codegen: 'LLVMCodegen', range_expr) -> ir.Value:
    """A range as a value: its plan (`emit_range`, the one formula) in a heap state."""
    from sushi_lang.backend.ranges import emit_range
    span = emit_range(codegen, range_expr)
    i32 = codegen.types.i32
    state_type = _range_state_type(codegen)
    raw, state = _heap_state(codegen, state_type)
    b = codegen.builder
    for field, value in ((_RANGE_K, ir.Constant(i32, 0)), (_RANGE_COUNT, span.count),
                         (_RANGE_FIRST, span.first), (_RANGE_STEP, span.step)):
        b.store(value, gep_utils.gep_struct_field(codegen, state, field, "range_field"))
    return iterator_value(codegen, _range_next(codegen), raw, _free_state(codegen),
                          _range_clone(codegen))


def _range_state_type(codegen: 'LLVMCodegen') -> ir.LiteralStructType:
    i32 = codegen.types.i32
    return ir.LiteralStructType([i32, i32, i32, i32])


def _range_next(codegen: 'LLVMCodegen') -> ir.Function:
    from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some
    from sushi_lang.semantics.typesys import BuiltinType

    def body(fn: ir.Function) -> None:
        b = codegen.builder
        state = b.bitcast(fn.args[0], ir.PointerType(_range_state_type(codegen)))

        def field(index: int) -> ir.Value:
            return gep_utils.gep_struct_field(codegen, state, index, "range_field")

        k_ptr = field(_RANGE_K)
        k = b.load(k_ptr, name="k")
        found = fn.append_basic_block("next.found")
        done = fn.append_basic_block("next.done")
        b.cbranch(b.icmp_signed("<", k, b.load(field(_RANGE_COUNT), name="count")),
                  found, done)

        b.position_at_end(found)
        b.store(b.add(k, ir.Constant(codegen.types.i32, 1)), k_ptr)
        item = b.add(b.load(field(_RANGE_FIRST), name="first"),
                     b.mul(b.load(field(_RANGE_STEP), name="step"), k), name="item")
        b.ret(emit_maybe_some(codegen, BuiltinType.I32, item))

        b.position_at_end(done)
        b.ret(emit_maybe_none(codegen, BuiltinType.I32))

    return _function(codegen, "__sushi_iter_next_range",
                     _next_type(codegen, BuiltinType.I32), body)


def _range_clone(codegen: 'LLVMCodegen') -> ir.Function:
    def body(fn: ir.Function) -> None:
        b = codegen.builder
        state_type = _range_state_type(codegen)
        raw, state = _heap_state(codegen, state_type)
        b.store(b.load(b.bitcast(fn.args[0], ir.PointerType(state_type))), state)
        b.ret(iterator_value(codegen, _range_next(codegen), raw, _free_state(codegen), fn))

    return _function(codegen, "__sushi_iter_clone_range", _clone_type(codegen), body)


def emit_new_iterator(codegen: 'LLVMCodegen', value: ir.Value, value_type: 'Type',
                      item_type: 'Type', next_method: ir.Function,
                      by_pointer: bool) -> ir.Value:
    """`Iterator.new(nom x)`: `x` on the heap, and `next_fn` calls its `next()`."""
    raw, state = _heap_state(codegen, codegen.types.ll_type(value_type))
    codegen.builder.store(value, state)
    return iterator_value(
        codegen, _new_next(codegen, value_type, item_type, next_method, by_pointer), raw,
        _new_drop(codegen, value_type), _new_clone(codegen, value_type, item_type,
                                                   next_method, by_pointer))


def _new_next(codegen: 'LLVMCodegen', value_type: 'Type', item_type: 'Type',
              next_method: ir.Function, by_pointer: bool) -> ir.Function:
    def body(fn: ir.Function) -> None:
        b = codegen.builder
        state = b.bitcast(fn.args[0], ir.PointerType(codegen.types.ll_type(value_type)))
        receiver = state if by_pointer else b.load(state, name="value")
        receiver = codegen.utils.cast_for_param(receiver, next_method.args[0].type)
        b.ret(b.call(next_method, [receiver], name="answer"))

    return _function(codegen, lifecycle_symbol("__sushi_iter_next_new_", value_type),
                     _next_type(codegen, item_type), body)


def _new_drop(codegen: 'LLVMCodegen', value_type: 'Type') -> ir.Function:
    from sushi_lang.backend.destructors import emit_value_destructor

    def body(fn: ir.Function) -> None:
        b = codegen.builder
        state = b.bitcast(fn.args[0], ir.PointerType(codegen.types.ll_type(value_type)))
        emit_value_destructor(codegen, state, value_type)
        emit_free(b, codegen, fn.args[0])
        b.ret_void()

    return _function(codegen, lifecycle_symbol("__sushi_iter_drop_new_", value_type),
                     _drop_type(codegen), body)


def _new_clone(codegen: 'LLVMCodegen', value_type: 'Type', item_type: 'Type',
               next_method: ir.Function, by_pointer: bool) -> ir.Function:
    """`clone_fn` of `Iterator.new`: a deep copy of the value it walks."""
    from sushi_lang.backend.ownership import copy_out

    def body(fn: ir.Function) -> None:
        b = codegen.builder
        if _holds_a_resource(codegen, value_type):
            codegen.runtime.errors.emit_runtime_error("RE2027")
            b.unreachable()
            return
        ll = codegen.types.ll_type(value_type)
        raw, state = _heap_state(codegen, ll)
        b.store(copy_out(codegen, b.bitcast(fn.args[0], ir.PointerType(ll)), value_type),
                state)
        b.ret(iterator_value(codegen,
                             _new_next(codegen, value_type, item_type, next_method,
                                       by_pointer),
                             raw, _new_drop(codegen, value_type), fn))

    return _function(codegen, lifecycle_symbol("__sushi_iter_clone_new_", value_type),
                     _clone_type(codegen), body)


def _holds_a_resource(codegen: 'LLVMCodegen', value_type: 'Type') -> bool:
    """Does a value of this type hold a resource, and so has no deep copy (CE2431)?"""
    from sushi_lang.backend.destructors import resolve_named_type
    from sushi_lang.backend.ownership import drops_of
    from sushi_lang.semantics.typesys import holds_declared_resource
    return holds_declared_resource(resolve_named_type(codegen, value_type), drops_of(codegen),
                                   resolve=lambda t: resolve_named_type(codegen, t))
