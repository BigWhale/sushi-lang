"""Memory management operations for the Sushi language compiler."""
from __future__ import annotations
import itertools
from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from sushi_lang.backend.constants import INT64_BIT_WIDTH
from sushi_lang.semantics.typesys import (
    ArrayType, StructType, DynamicArrayType, EnumType, Type,
)
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.memory.heap import emit_malloc

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def get_element_size_constant(codegen: 'LLVMCodegen', element_type: ir.Type) -> ir.Value:
    """Get the size in bytes of an element type as an LLVM constant."""
    # Read the width off the type. The chain of `==` comparisons that stood here named
    # i32 and i8 and no other integer, so `i16[]`, `i64[]`, `u16[]`, `u64[]` and the four
    # matching `List@(T)` instantiations reached the CE0079 below -- an internal error on
    # ordinary code (#375). `calculate_llvm_type_size` was total over `ir.IntType` all
    # along, which is what made the two siblings disagree.
    if isinstance(element_type, ir.IntType):
        return ir.Constant(codegen.types.i32, element_type.width // 8)
    elif isinstance(element_type, ir.PointerType):
        return ir.Constant(codegen.types.i32, 8)  # pointer = 8 bytes (64-bit)
    elif isinstance(element_type, ir.FloatType):
        return ir.Constant(codegen.types.i32, 4)  # f32 = 4 bytes
    elif isinstance(element_type, ir.DoubleType):
        return ir.Constant(codegen.types.i32, 8)  # f64 = 8 bytes

    # `getelementptr(null, 1)` is the offset of the second element, i.e. one element's
    # padded size. BaseStructType, not LiteralStructType: a user struct is an IDENTIFIED
    # type and a SIBLING rather than a subclass, so the narrower check sent every one to
    # the CE0079 below (#257).
    elif isinstance(element_type, ir.types.BaseStructType):
        null_ptr = ir.Constant(ir.PointerType(element_type), None)
        size_gep = codegen.builder.gep(
            null_ptr,
            [ir.Constant(codegen.types.i64, 1)],
            name="size_gep"
        )
        size_i64 = codegen.builder.ptrtoint(size_gep, codegen.types.i64, name="size_i64")
        size_i32 = codegen.builder.trunc(size_i64, codegen.types.i32, name="size_i32")
        return size_i32

    else:
        raise_internal_error("CE0079", type=str(element_type))


def calculate_llvm_type_size(llvm_type: 'ir.Type') -> int:
    """Calculate the size in bytes of an LLVM type for offset calculations."""
    if isinstance(llvm_type, ir.IntType):
        return llvm_type.width // 8
    elif isinstance(llvm_type, ir.PointerType):
        return 8
    elif isinstance(llvm_type, ir.FloatType):
        return 4
    elif isinstance(llvm_type, ir.DoubleType):
        return 8
    elif isinstance(llvm_type, ir.types.BaseStructType):
        # The ALIGNED sizeof (16), not the field sum (13): the owned byte at offset 12 must
        # survive a round-trip through an enum payload sized from this (#145).
        #
        # The sniff stays on the LITERAL type deliberately -- a string is an anonymous fat
        # pointer, so a user struct shaped `{i8*, i32, i8}` must not be mistaken for one.
        els = llvm_type.elements
        if (isinstance(llvm_type, ir.LiteralStructType)
                and len(els) == 3 and isinstance(els[0], ir.PointerType)
                and isinstance(els[1], ir.IntType) and els[1].width == 32
                and isinstance(els[2], ir.IntType) and els[2].width == 8):
            return 16
        total_size = 0
        for element_type in llvm_type.elements:
            total_size += calculate_llvm_type_size(element_type)
        return total_size
    elif isinstance(llvm_type, ir.ArrayType):
        element_size = calculate_llvm_type_size(llvm_type.element)
        return element_size * llvm_type.count
    else:
        return 16


def emit_realloc_call(codegen: 'LLVMCodegen', old_ptr: ir.Value, new_size: ir.Value) -> ir.Value:
    """Emit realloc() call with error checking."""
    realloc_func = codegen.get_realloc_func()

    if old_ptr.type != ir.PointerType(codegen.types.i8):
        old_ptr = codegen.builder.bitcast(old_ptr, ir.PointerType(codegen.types.i8), name="old_void_ptr")

    if new_size.type != ir.IntType(INT64_BIT_WIDTH):
        new_size = codegen.builder.zext(new_size, ir.IntType(INT64_BIT_WIDTH), name="size_i64")

    new_void_ptr = codegen.builder.call(realloc_func, [old_ptr, new_size], name="realloc_result")

    null_ptr = ir.Constant(ir.PointerType(codegen.types.i8), None)
    is_null = codegen.builder.icmp_unsigned('==', new_void_ptr, null_ptr, name="is_null")

    null_block = codegen.builder.append_basic_block(name="realloc_null")
    success_block = codegen.builder.append_basic_block(name="realloc_success")

    codegen.builder.cbranch(is_null, null_block, success_block)

    codegen.builder.position_at_end(null_block)
    codegen.runtime.errors.emit_runtime_error("RE2021")
    codegen.builder.unreachable()

    codegen.builder.position_at_end(success_block)

    return new_void_ptr


def emit_free_call(codegen: 'LLVMCodegen', ptr: ir.Value) -> None:
    """Emit free() call to deallocate memory."""
    free_func = codegen.get_free_func()
    codegen.builder.call(free_func, [ptr])


def clone_dynamic_array_value(codegen: 'LLVMCodegen', array_struct: ir.Value, element_type: Type) -> ir.Value:
    """Clone a dynamic array struct value (creates deep copy with independent memory)."""
    zero = ir.Constant(codegen.types.i32, 0)

    source_len = codegen.builder.extract_value(array_struct, 0)
    source_cap = codegen.builder.extract_value(array_struct, 1)
    source_data_ptr = codegen.builder.extract_value(array_struct, 2)

    element_llvm_type = codegen.types.ll_type(element_type)
    array_struct_type = array_struct.type

    len_is_zero = codegen.builder.icmp_unsigned('==', source_len, zero)

    empty_clone_bb = codegen.builder.append_basic_block('clone_empty')
    non_empty_clone_bb = codegen.builder.append_basic_block('clone_non_empty')
    clone_merge_bb = codegen.builder.append_basic_block('clone_merge')

    codegen.builder.cbranch(len_is_zero, empty_clone_bb, non_empty_clone_bb)

    codegen.builder.position_at_end(empty_clone_bb)
    null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)
    empty_array = ir.Constant(array_struct_type, ir.Undefined)
    empty_array = codegen.builder.insert_value(empty_array, zero, 0)
    empty_array = codegen.builder.insert_value(empty_array, zero, 1)
    empty_array = codegen.builder.insert_value(empty_array, null_ptr, 2)
    codegen.builder.branch(clone_merge_bb)

    codegen.builder.position_at_end(non_empty_clone_bb)

    sizeof_element_i32 = codegen.types.get_type_size_constant(element_type)
    cap_i64 = codegen.builder.zext(source_cap, codegen.types.i64)
    sizeof_element_i64 = codegen.builder.zext(sizeof_element_i32, codegen.types.i64)
    total_bytes = codegen.builder.mul(cap_i64, sizeof_element_i64)

    new_data_ptr_i8 = emit_malloc(codegen, codegen.builder, total_bytes)
    new_data_ptr = codegen.builder.bitcast(new_data_ptr_i8, ir.PointerType(element_llvm_type))

    copy_index = codegen.builder.alloca(codegen.types.i32, name="copy_idx")
    codegen.builder.store(zero, copy_index)

    copy_loop_head = codegen.builder.append_basic_block('copy_loop_head')
    copy_loop_body = codegen.builder.append_basic_block('copy_loop_body')
    copy_loop_exit = codegen.builder.append_basic_block('copy_loop_exit')

    codegen.builder.branch(copy_loop_head)

    codegen.builder.position_at_end(copy_loop_head)
    idx = codegen.builder.load(copy_index)
    cond = codegen.builder.icmp_unsigned('<', idx, source_len)
    codegen.builder.cbranch(cond, copy_loop_body, copy_loop_exit)

    # An owning element must get its OWN buffers, or the clone and the source share them
    # and both free at scope exit. `emit_value_clone` is a no-op for a non-owning element
    # and recursion-safe for a self-referential one.
    codegen.builder.position_at_end(copy_loop_body)
    src_elem_ptr = codegen.builder.gep(source_data_ptr, [idx])
    elem = codegen.builder.load(src_elem_ptr)
    elem = emit_value_clone(codegen, elem, element_type)
    dst_elem_ptr = codegen.builder.gep(new_data_ptr, [idx])
    codegen.builder.store(elem, dst_elem_ptr)

    next_idx = codegen.builder.add(idx, ir.Constant(codegen.types.i32, 1))
    codegen.builder.store(next_idx, copy_index)
    codegen.builder.branch(copy_loop_head)

    codegen.builder.position_at_end(copy_loop_exit)
    new_array = ir.Constant(array_struct_type, ir.Undefined)
    new_array = codegen.builder.insert_value(new_array, source_len, 0)
    new_array = codegen.builder.insert_value(new_array, source_cap, 1)
    new_array = codegen.builder.insert_value(new_array, new_data_ptr, 2)
    codegen.builder.branch(clone_merge_bb)

    codegen.builder.position_at_end(clone_merge_bb)
    result_phi = codegen.builder.phi(array_struct_type, name="cloned_array")
    result_phi.add_incoming(empty_array, empty_clone_bb)
    result_phi.add_incoming(new_array, copy_loop_exit)

    return result_phi


def is_container_get_call(codegen: 'LLVMCodegen', expr) -> bool:
    """Is `expr` a `.get()` that READS OUT of storage its receiver still owns?"""
    from sushi_lang.semantics.ast import TryExpr
    while isinstance(expr, TryExpr):
        expr = expr.expr

    if getattr(expr, "method", None) != "get":
        return False
    receiver = getattr(expr, "receiver", None)
    if receiver is None:
        return False
    from sushi_lang.semantics.ownership import is_get_out_container
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type
    from sushi_lang.backend.expressions.calls.utils import infer_generic_struct_type

    receiver_type = infer_expr_semantic_type(codegen, receiver)
    if receiver_type is None:
        # The generic containers resolve through the same helper their emitters use
        # (`calls/utils.py`), so this predicate and `try_emit_own_method` cannot disagree
        # about what an `Own` / `List` / `HashMap` receiver is. It is AST-only and emits no
        # IR, which is what makes it safe to call from a predicate.
        from sushi_lang.semantics.generics.cloning import CONTAINER_PREFIXES
        for prefix in CONTAINER_PREFIXES:
            receiver_type = infer_generic_struct_type(codegen, receiver, prefix)
            if receiver_type is not None:
                break
    return is_get_out_container(receiver_type)


def expression_is_temporary(codegen: 'LLVMCodegen', expr) -> bool:
    """Does `expr` produce a value that NO other owner will free?"""
    from sushi_lang.semantics.ast import Name, MemberAccess, IndexAccess
    if isinstance(expr, (Name, MemberAccess, IndexAccess)):
        return False
    return not is_container_get_call(codegen, expr)


_PARK_SEQ = itertools.count()


def own_temporary(codegen: 'LLVMCodegen', expr, value: ir.Value,
                  semantic_type: Optional[Type],
                  slot_type: Optional[ir.Type] = None) -> Optional[ir.Value]:
    """Give a value NOBODY else will free an owner, and return its slot; None if it has one.

    THE ownership decision for a value no binding names, and `expression_is_temporary` is
    the predicate that makes it: a value nobody else will free is registered like any
    owning local, so scope exit frees it exactly once; a value read out of storage someone
    else owns gets no owner here, because a second one would be a double free.

    Registration goes through `register_owning_value`, the COMPLETE registry router.
    `create_local` reaches only `register_local_cleanup`, which knows structs with owning
    fields, fixed arrays, closures and strings -- so a dynamic array, a `List@(T)` and an
    `Own@(T)` given an owner here were registered NOWHERE and leaked (#382).
    """
    from sushi_lang.backend.destructors import needs_cleanup, resolve_named_type

    if value is None or semantic_type is None:
        return None
    resolved = resolve_named_type(codegen, semantic_type)
    if resolved is None or not needs_cleanup(resolved):
        return None
    if not expression_is_temporary(codegen, expr):
        return None

    name = f"__temp_{next(_PARK_SEQ)}"
    slot = codegen.memory.create_local(name, slot_type or value.type, value, resolved,
                                       register_cleanup=False)
    codegen.memory.register_owning_value(name, resolved, slot)
    return slot


def park_value(codegen: 'LLVMCodegen', expr, value: ir.Value,
               semantic_type: Optional[Type],
               slot_type: Optional[ir.Type] = None) -> ir.Value:
    """Park a value that names no storage in a slot, giving it an OWNER when it needs one."""
    slot = own_temporary(codegen, expr, value, semantic_type, slot_type)
    if slot is not None:
        return slot

    # An ENTRY-block slot, not one per block: a receiver inside a `while` body would
    # otherwise allocate on every iteration and grow the stack without bound.
    slot = codegen.memory.entry_alloca(slot_type or value.type, "temp_slot")
    codegen.builder.store(value, slot)
    return slot


def destroy_enum_temp(codegen: 'LLVMCodegen', expr_ast, enum_value: ir.Value,
                      enum_type: Type) -> None:
    """Free an unbound Result/Maybe temporary whose payload is never extracted (#159)."""
    from sushi_lang.backend.destructors import (
        emit_value_destructor, needs_cleanup, resolve_named_type
    )

    if not expression_is_temporary(codegen, expr_ast):
        return

    # `needs_cleanup` is table-free: an unresolved UnknownType answers False, which is exactly
    # how a Result's owning payload escaped every RAII predicate in #179. Resolve first.
    resolved = resolve_named_type(codegen, enum_type)
    if not isinstance(resolved, EnumType) or not needs_cleanup(resolved):
        return

    # emit_value_destructor takes a POINTER, and the temporary is an SSA aggregate. Park it in an
    # ENTRY-block alloca: a `while` condition is re-entered every iteration, and an alloca emitted
    # there would allocate per iteration and grow the stack without bound.
    slot = codegen.memory.entry_alloca(enum_value.type, "enum_temp_slot")
    codegen.builder.store(enum_value, slot)
    emit_value_destructor(codegen, slot, resolved)


def emit_value_clone(codegen: 'LLVMCodegen', value: ir.Value, value_type: Type) -> ir.Value:
    """Return a deep copy of `value` that owns independent heap buffers."""
    from sushi_lang.semantics.typesys import (
        UnknownType, BuiltinType, ForeignPtrType, EnumType, FunctionType
    )

    if isinstance(value_type, UnknownType):
        # A named type may be a struct OR an enum; resolve against both tables, else an
        # owning enum passed as UnknownType would fall through as a no-op and not be
        # deep-copied (double-free on a shared payload, #139).
        value_type = (codegen.struct_table.by_name.get(value_type.name)
                      or codegen.enum_table.by_name.get(value_type.name)
                      or value_type)

    if isinstance(value_type, ForeignPtrType):
        return value

    # Function value: duplicate the heap environment through the type-erased
    # `clone_ptr` slot, mirroring how the destructor frees it through `drop_ptr`.
    # A non-capturing value carries a null clone_ptr and passes through unchanged.
    if isinstance(value_type, FunctionType):
        return _clone_function_value(codegen, value)

    if isinstance(value_type, BuiltinType):
        if value_type == BuiltinType.STRING:
            return _clone_string_value(codegen, value)
        return value  # numerics, bool, I/O handles: nothing to clone

    if isinstance(value_type, (DynamicArrayType, ArrayType, StructType, EnumType)):
        return _emit_composite_clone(codegen, value, value_type)

    return value


def _clone_struct_value_dispatch(codegen: 'LLVMCodegen', value: ir.Value,
                                 value_type: Type) -> ir.Value:
    """The struct kind's clone handler: containers first, then the field-walk clone."""
    from sushi_lang.semantics.generics.cloning import CONTAINER_PREFIXES
    if value_type.name.startswith(CONTAINER_PREFIXES):
        if value_type.name.startswith("Own<"):
            return _clone_own_value(codegen, value, value_type)
        if value_type.name.startswith("List<"):
            return _clone_list_value(codegen, value, value_type)
        return _clone_hashmap_value(codegen, value, value_type)
    return _clone_struct_value(codegen, value, value_type)


def _emit_composite_clone(codegen: 'LLVMCodegen', value: ir.Value, value_type: Type) -> ir.Value:
    """Deep-clone a composite type, breaking self-referential cycles."""
    from sushi_lang.backend import lifecycle
    key = lifecycle.composite_type_key(value_type)
    stack = getattr(codegen, "_clone_inprogress", None)
    if stack is None:
        stack = []
        codegen._clone_inprogress = stack

    if key in stack:
        fn = lifecycle.get_or_emit_lifecycle_func(codegen, value_type, "clone")
        return codegen.builder.call(fn, [value])

    stack.append(key)
    try:
        return lifecycle.inline_clone(codegen, value, value_type)
    finally:
        stack.pop()


def _declare_memcpy(codegen: 'LLVMCodegen'):
    """Declare the i64-length llvm.memcpy intrinsic (safe on ARM64, see #149)."""
    i8_ptr = ir.PointerType(codegen.types.i8)
    return codegen.module.declare_intrinsic(
        'llvm.memcpy', [i8_ptr, i8_ptr, ir.IntType(INT64_BIT_WIDTH)]
    )


def _clone_string_value(codegen: 'LLVMCodegen', fat: ir.Value) -> ir.Value:
    """Deep-copy a string's buffer, UNCONDITIONALLY."""
    b = codegen.builder
    size = b.extract_value(fat, 1, name="clone_str_size")
    data = b.extract_value(fat, 0, name="clone_str_data")

    size_i64 = b.zext(size, ir.IntType(INT64_BIT_WIDTH))
    new_data = emit_malloc(codegen, codegen.builder, size_i64)  # i8*
    b.call(_declare_memcpy(codegen),
           [new_data, data, size_i64, ir.Constant(ir.IntType(1), 0)])
    cloned = b.insert_value(fat, new_data, 0)
    cloned = b.insert_value(cloned, ir.Constant(codegen.types.i8, 1), 2)
    return cloned


def _clone_function_value(codegen: 'LLVMCodegen', fat: ir.Value) -> ir.Value:
    """Duplicate a closure's heap environment through its `clone_ptr` slot."""
    b = codegen.builder
    clone_ptr = b.extract_value(fat, 3, name="closure_clone")
    env_ptr = b.extract_value(fat, 1, name="closure_env")

    slot = b.alloca(fat.type, name="clone_closure_slot")
    b.store(fat, slot)  # default: return the input unchanged (clone_ptr == null)

    has_clone = b.icmp_unsigned("!=", clone_ptr, ir.Constant(clone_ptr.type, None))
    with b.if_then(has_clone):
        clone_fn_ty = ir.FunctionType(codegen.types.str_ptr, [codegen.types.str_ptr])
        callee = b.bitcast(clone_ptr, ir.PointerType(clone_fn_ty), name="closure_clone_fn")
        new_env = b.call(callee, [env_ptr], name="cloned_env")
        b.store(b.insert_value(fat, new_env, 1, name="cloned_closure"), slot)

    return b.load(slot, name="cloned_function_value")


def _clone_own_value(codegen: 'LLVMCodegen', value: ir.Value, value_type: StructType) -> ir.Value:
    """Deep-copy an Own<T>: mirror the destructor's Own path (recurse pointee, own ptr)."""
    from sushi_lang.semantics.generics.own import get_own_element_type

    b = codegen.builder
    elem_ty = get_own_element_type(value_type)
    elem_llvm = codegen.types.ll_type(elem_ty)
    ptr = b.extract_value(value, 0, name="clone_own_ptr")  # T*

    slot = b.alloca(value.type, name="clone_own_slot")
    b.store(value, slot)  # default: passthrough (null ptr)

    is_not_null = b.icmp_unsigned("!=", ptr, ir.Constant(ptr.type, None))
    with b.if_then(is_not_null):
        pointee = b.load(ptr, name="own_pointee")
        cloned_pointee = emit_value_clone(codegen, pointee, elem_ty)
        new_raw = emit_malloc(codegen, codegen.builder, codegen.types.get_type_size_constant(elem_ty))
        new_ptr = b.bitcast(new_raw, ir.PointerType(elem_llvm), name="own_new_ptr")
        b.store(cloned_pointee, new_ptr)
        b.store(b.insert_value(value, new_ptr, 0), slot)

    return b.load(slot, name="cloned_own")


def _clone_list_value(codegen: 'LLVMCodegen', value: ir.Value, value_type: StructType) -> ir.Value:
    """Deep-copy a List<T>: allocate a fresh buffer and copy the elements."""
    from sushi_lang.backend.generics.list.types import extract_element_type
    from sushi_lang.backend.destructors import field_needs_cleanup
    from sushi_lang.backend.generics.container_walk import emit_container_walk

    b = codegen.builder
    elem_ty = extract_element_type(value_type, codegen)
    elem_llvm = codegen.types.ll_type(elem_ty)
    length = b.extract_value(value, 0, name="clone_list_len")
    cap = b.extract_value(value, 1, name="clone_list_cap")
    data = b.extract_value(value, 2, name="clone_list_data")  # T*

    slot = b.alloca(value.type, name="clone_list_slot")
    b.store(value, slot)  # default: passthrough (null data)

    is_not_null = b.icmp_unsigned("!=", data, ir.Constant(data.type, None))
    with b.if_then(is_not_null):
        elem_size_i64 = b.zext(codegen.types.get_type_size_constant(elem_ty),
                               ir.IntType(INT64_BIT_WIDTH))
        cap_i64 = b.zext(cap, ir.IntType(INT64_BIT_WIDTH))
        total_bytes = b.mul(cap_i64, elem_size_i64)
        new_raw = emit_malloc(codegen, codegen.builder, total_bytes)
        new_data = b.bitcast(new_raw, ir.PointerType(elem_llvm), name="list_new_data")

        len_i64 = b.zext(length, ir.IntType(INT64_BIT_WIDTH))
        bytes_to_copy = b.mul(len_i64, elem_size_i64)
        old_i8 = b.bitcast(data, ir.PointerType(codegen.types.i8), name="list_old_i8")
        b.call(_declare_memcpy(codegen),
               [new_raw, old_i8, bytes_to_copy, ir.Constant(ir.IntType(1), 0)])

        if field_needs_cleanup(codegen, elem_ty):
            def clone_element(element_ptr: ir.Value, _index: ir.Value) -> None:
                loaded = codegen.builder.load(element_ptr, name="list_clone_elem")
                cloned = emit_value_clone(codegen, loaded, elem_ty)
                codegen.builder.store(cloned, element_ptr)

            emit_container_walk(codegen, new_data, length, clone_element,
                                prefix="list_clone")

        codegen.builder.store(codegen.builder.insert_value(value, new_data, 2), slot)

    return b.load(slot, name="cloned_list")


def _clone_hashmap_value(codegen: 'LLVMCodegen', value: ir.Value, value_type: StructType) -> ir.Value:
    """Deep-copy a HashMap<K, V>: fresh bucket buffer, deep-cloned owning keys/values."""
    from sushi_lang.semantics.generics.hashmap import extract_key_value_types
    from sushi_lang.backend.generics.hashmap.types import get_entry_type, ENTRY_OCCUPIED
    from sushi_lang.backend.generics.hashmap.utils import emit_entry_state_check
    from sushi_lang.backend.generics.container_walk import emit_container_walk
    from sushi_lang.backend.constants import ENTRY_KEY_INDICES, ENTRY_VALUE_INDICES

    b = codegen.builder
    key_type, val_type = extract_key_value_types(value_type, codegen)
    entry_llvm = get_entry_type(codegen, key_type, val_type)

    buckets = b.extract_value(value, 0, name="clone_hm_buckets")   # {len, cap, Entry*}
    capacity = b.extract_value(value, 2, name="clone_hm_cap")      # outer capacity field
    data = b.extract_value(buckets, 2, name="clone_hm_data")       # Entry*

    slot = b.alloca(value.type, name="clone_hm_slot")
    b.store(value, slot)  # default: passthrough (null data)

    is_not_null = b.icmp_unsigned("!=", data, ir.Constant(data.type, None))
    with b.if_then(is_not_null):
        entry_size_i64 = b.zext(get_element_size_constant(codegen, entry_llvm),
                                ir.IntType(INT64_BIT_WIDTH))
        cap_i64 = b.zext(capacity, ir.IntType(INT64_BIT_WIDTH))
        total_bytes = b.mul(cap_i64, entry_size_i64)
        new_raw = emit_malloc(codegen, codegen.builder, total_bytes)
        new_data = codegen.builder.bitcast(new_raw, ir.PointerType(entry_llvm),
                                           name="hm_new_data")

        old_i8 = codegen.builder.bitcast(data, ir.PointerType(codegen.types.i8),
                                         name="hm_old_i8")
        codegen.builder.call(_declare_memcpy(codegen),
                             [new_raw, old_i8, total_bytes, ir.Constant(ir.IntType(1), 0)])

        def occupied(entry_ptr: ir.Value, _index: ir.Value) -> ir.Value:
            return emit_entry_state_check(codegen, entry_ptr, ENTRY_OCCUPIED, "hm_occupied")

        def clone_entry(entry_ptr: ir.Value, _index: ir.Value) -> None:
            key_ptr = codegen.builder.gep(entry_ptr, ENTRY_KEY_INDICES, name="hm_key_ptr")
            val_ptr = codegen.builder.gep(entry_ptr, ENTRY_VALUE_INDICES, name="hm_val_ptr")
            k = codegen.builder.load(key_ptr, name="hm_key")
            codegen.builder.store(emit_value_clone(codegen, k, key_type), key_ptr)
            v = codegen.builder.load(val_ptr, name="hm_val")
            codegen.builder.store(emit_value_clone(codegen, v, val_type), val_ptr)

        emit_container_walk(codegen, new_data, capacity, clone_entry,
                            should_visit=occupied, prefix="hm_clone")

        new_buckets = codegen.builder.insert_value(buckets, new_data, 2, name="hm_new_buckets")
        codegen.builder.store(
            codegen.builder.insert_value(value, new_buckets, 0), slot)

    return b.load(slot, name="cloned_hashmap")


def _clone_fixed_array_value(codegen: 'LLVMCodegen', value: ir.Value,
                             value_type: 'ArrayType') -> ir.Value:
    """Deep-copy a fixed array `T[N]` element by element."""
    from sushi_lang.backend.destructors import field_needs_cleanup

    if not field_needs_cleanup(codegen, value_type.base_type):
        return value

    b = codegen.builder
    new_array = value
    for i in range(value_type.size):
        elem = b.extract_value(value, i, name=f"clone_elem_{i}")
        cloned = emit_value_clone(codegen, elem, value_type.base_type)
        new_array = b.insert_value(new_array, cloned, i, name=f"cloned_elem_{i}")
    return new_array


def _clone_struct_value(codegen: 'LLVMCodegen', value: ir.Value, value_type: StructType) -> ir.Value:
    """Deep-copy a regular struct field-by-field, recursing through emit_value_clone."""
    from sushi_lang.backend.destructors import field_needs_cleanup

    b = codegen.builder
    new_struct = value
    for i, (_field_name, field_type) in enumerate(value_type.fields):
        if field_needs_cleanup(codegen, field_type):
            field_val = b.extract_value(value, i, name=f"clone_field_{i}")
            cloned = emit_value_clone(codegen, field_val, field_type)
            new_struct = b.insert_value(new_struct, cloned, i, name=f"cloned_field_{i}")
    return new_struct


def _clone_enum_value(codegen: 'LLVMCodegen', value: ir.Value, value_type) -> ir.Value:
    """Deep-copy an enum by cloning the active variant's owning associated data."""
    from sushi_lang.backend.destructors import field_needs_cleanup
    from sushi_lang.backend.constants.llvm_values import ZERO_I32, ONE_I32, make_i32_const

    b = codegen.builder
    # Resolve the payload type before gating, exactly as `_emit_enum_destructor` does. An
    # `Own<IntList>` payload arrives as an unresolved name, and an unresolved name answers
    # "owns nothing" -- so the destructor (which resolves) would free it while the clone
    # (which did not) handed out a shallow copy sharing the same pointer: double free (#183).
    variants_nc = [
        (i, v) for i, v in enumerate(value_type.variants)
        if v.associated_types and any(field_needs_cleanup(codegen, t) for t in v.associated_types)
    ]
    if not variants_nc:
        return value  # no owning payload in any variant -> nothing to clone

    slot = b.alloca(value.type, name="clone_enum_slot")
    b.store(value, slot)

    tag_ptr = b.gep(slot, [ZERO_I32, ZERO_I32], name="clone_enum_tag_ptr")
    tag = b.load(tag_ptr, name="clone_enum_tag")
    data_ptr = b.gep(slot, [ZERO_I32, ONE_I32], name="clone_enum_data_ptr")

    end_bb = b.append_basic_block(name="enum_clone_end")
    switch = b.switch(tag, end_bb)

    for tag_val, variant in variants_nc:
        case_bb = b.append_basic_block(name=f"clone_variant_{variant.name}")
        switch.add_case(make_i32_const(tag_val), case_bb)
        b.position_at_end(case_bb)

        # Field offsets from the ONE layout authority (#300 phase 2).
        field_offsets = codegen.types.payload_field_offsets(variant.associated_types)
        for assoc_type, field_offset in zip(variant.associated_types, field_offsets, strict=True):
            if field_needs_cleanup(codegen, assoc_type):
                data_i8_ptr = b.bitcast(data_ptr, ir.PointerType(ir.IntType(8)),
                                        name="clone_enum_data_i8")
                field_i8_ptr = b.gep(data_i8_ptr, [make_i32_const(field_offset)],
                                     name="clone_enum_field_i8")
                field_llvm = codegen.types.ll_type(assoc_type)
                field_ptr = b.bitcast(field_i8_ptr, ir.PointerType(field_llvm),
                                      name="clone_enum_field_ptr")
                orig = b.load(field_ptr, name="clone_enum_orig")
                b.store(emit_value_clone(codegen, orig, assoc_type), field_ptr)

        b.branch(end_bb)

    b.position_at_end(end_bb)
    return b.load(slot, name="cloned_enum")


# The CLONE half of every composite kind's handler; the DESTROY half registers in
# backend/destructors.py. A kind registered on one side only is a double free or a leak by
# construction, so tests/unit/test_lifecycle_handlers.py asserts the pairing.
from sushi_lang.backend.lifecycle import register_lifecycle as _register_lifecycle  # noqa: E402

_register_lifecycle(
    "dynamic_array",
    clone=lambda cg, v, ty: clone_dynamic_array_value(cg, v, ty.base_type),
)
_register_lifecycle("fixed_array", clone=_clone_fixed_array_value)
_register_lifecycle("struct", clone=_clone_struct_value_dispatch)
_register_lifecycle("enum", clone=_clone_enum_value)
