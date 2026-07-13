"""
Memory management operations for the Sushi language compiler.

This module handles memory allocation, deallocation, cloning, and size calculations
for dynamic arrays and structs. Includes malloc/realloc/free wrappers with error checking.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT64_BIT_WIDTH
from sushi_lang.semantics.typesys import StructType, DynamicArrayType, EnumType, Type
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.memory.heap import emit_malloc

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


# Type size lookup table (simplified dispatch for common types)
TYPE_SIZES = {
    'i8': 1, 'i16': 2, 'i32': 4, 'i64': 8,
    'u8': 1, 'u16': 2, 'u32': 4, 'u64': 8,
    'f32': 4, 'f64': 8,
    'bool': 1, 'ptr': 8
}


def get_element_size_constant(codegen: 'LLVMCodegen', element_type: ir.Type) -> ir.Value:
    """Get the size in bytes of an element type as an LLVM constant.

    Uses dispatch table for common types, falls back to LLVM's GEP trick
    for complex types like structs (which accounts for padding).

    Args:
        codegen: The LLVM codegen instance.
        element_type: The LLVM element type.

    Returns:
        The size as i32 constant.

    Raises:
        ValueError: If element type is not supported.
    """
    # Fast path: Check common types
    if element_type == codegen.types.i32:
        return ir.Constant(codegen.types.i32, 4)  # i32 = 4 bytes
    elif element_type == codegen.types.i8:
        return ir.Constant(codegen.types.i32, 1)  # i8 = 1 byte
    elif isinstance(element_type, ir.PointerType):
        return ir.Constant(codegen.types.i32, 8)  # pointer = 8 bytes (64-bit)
    elif isinstance(element_type, ir.FloatType):
        return ir.Constant(codegen.types.i32, 4)  # f32 = 4 bytes
    elif isinstance(element_type, ir.DoubleType):
        return ir.Constant(codegen.types.i32, 8)  # f64 = 8 bytes

    # Struct types: Use LLVM's GEP trick to get actual size with padding
    # getelementptr(null, 1) gives offset of second element = size of one element
    elif isinstance(element_type, ir.LiteralStructType):
        # Create a null pointer of type element_type*
        null_ptr = ir.Constant(ir.PointerType(element_type), None)
        # GEP to get pointer to element [1]
        size_gep = codegen.builder.gep(
            null_ptr,
            [ir.Constant(codegen.types.i64, 1)],
            name="size_gep"
        )
        # Convert pointer to integer to get the size
        size_i64 = codegen.builder.ptrtoint(size_gep, codegen.types.i64, name="size_i64")
        # Truncate to i32 (sizes should fit in 32 bits)
        size_i32 = codegen.builder.trunc(size_i64, codegen.types.i32, name="size_i32")
        return size_i32

    else:
        raise_internal_error("CE0079", type=str(element_type))


def calculate_llvm_type_size(llvm_type: 'ir.Type') -> int:
    """Calculate the size in bytes of an LLVM type for offset calculations.

    Recursively handles complex types including structs and arrays.
    This function provides accurate size calculations for all LLVM types,
    including nested structures.

    Args:
        llvm_type: The LLVM type to calculate size for.

    Returns:
        Size in bytes.
    """
    if isinstance(llvm_type, ir.IntType):
        return llvm_type.width // 8
    elif isinstance(llvm_type, ir.PointerType):
        return 8
    elif isinstance(llvm_type, ir.FloatType):
        return 4
    elif isinstance(llvm_type, ir.DoubleType):
        return 8
    elif isinstance(llvm_type, ir.LiteralStructType):
        # String fat pointer {i8*, i32, i8 owned}: use the ALIGNED sizeof (16), not the raw
        # field sum (13). The owned byte at offset 12 must survive a round-trip through an
        # enum/Result/Maybe payload, whose data array is sized from this (#145).
        els = llvm_type.elements
        if (len(els) == 3 and isinstance(els[0], ir.PointerType)
                and isinstance(els[1], ir.IntType) and els[1].width == 32
                and isinstance(els[2], ir.IntType) and els[2].width == 8):
            return 16
        # For structs (including enums), calculate total size
        total_size = 0
        for element_type in llvm_type.elements:
            total_size += calculate_llvm_type_size(element_type)
        return total_size
    elif isinstance(llvm_type, ir.ArrayType):
        # For arrays, multiply element size by count
        element_size = calculate_llvm_type_size(llvm_type.element)
        return element_size * llvm_type.count
    else:
        # Conservative estimate for unknown types
        return 16


def get_type_size(llvm_type: ir.Type) -> int:
    """Get the size in bytes of an LLVM type (simplified Python int).

    This is a lightweight version that returns a Python int rather than
    an LLVM constant. Used for quick size estimates.

    Args:
        llvm_type: The LLVM type.

    Returns:
        Size in bytes.
    """
    if isinstance(llvm_type, ir.IntType):
        return llvm_type.width // 8
    elif isinstance(llvm_type, ir.PointerType):
        return 8  # Assume 64-bit pointers
    elif isinstance(llvm_type, ir.FloatType):
        return 4
    elif isinstance(llvm_type, ir.DoubleType):
        return 8
    else:
        # For complex types, use accurate calculation
        return calculate_llvm_type_size(llvm_type)


def emit_realloc_call(codegen: 'LLVMCodegen', old_ptr: ir.Value, new_size: ir.Value) -> ir.Value:
    """Emit realloc() call with error checking.

    Args:
        codegen: The LLVM codegen instance.
        old_ptr: Previous pointer (may be null for initial allocation).
        new_size: New size in bytes (will be converted to i64 if needed).

    Returns:
        New allocated pointer (i8*).

    Note:
        Emits runtime error RE2021 and exits if realloc returns NULL.
    """
    realloc_func = codegen.get_realloc_func()

    # Cast old pointer to void* if needed
    if old_ptr.type != ir.PointerType(codegen.types.i8):
        old_ptr = codegen.builder.bitcast(old_ptr, ir.PointerType(codegen.types.i8), name="old_void_ptr")

    # Convert size to i64 for realloc (size_t)
    if new_size.type != ir.IntType(INT64_BIT_WIDTH):
        new_size = codegen.builder.zext(new_size, ir.IntType(INT64_BIT_WIDTH), name="size_i64")

    # Call realloc
    new_void_ptr = codegen.builder.call(realloc_func, [old_ptr, new_size], name="realloc_result")

    # Check if realloc returned NULL (allocation failure)
    null_ptr = ir.Constant(ir.PointerType(codegen.types.i8), None)
    is_null = codegen.builder.icmp_unsigned('==', new_void_ptr, null_ptr, name="is_null")

    # Create basic blocks for null check
    null_block = codegen.builder.append_basic_block(name="realloc_null")
    success_block = codegen.builder.append_basic_block(name="realloc_success")

    # Branch based on null check
    codegen.builder.cbranch(is_null, null_block, success_block)

    # Null block: emit runtime error and exit
    codegen.builder.position_at_end(null_block)
    codegen.runtime.errors.emit_runtime_error("RE2021")
    codegen.builder.unreachable()

    # Success block: continue normal execution
    codegen.builder.position_at_end(success_block)

    return new_void_ptr


def emit_free_call(codegen: 'LLVMCodegen', ptr: ir.Value) -> None:
    """Emit free() call to deallocate memory.

    Args:
        codegen: The LLVM codegen instance.
        ptr: Pointer to free (should be i8*/void*).
    """
    free_func = codegen.get_free_func()
    codegen.builder.call(free_func, [ptr])


def clone_dynamic_array_value(codegen: 'LLVMCodegen', array_struct: ir.Value, element_type: Type) -> ir.Value:
    """Clone a dynamic array struct value (creates deep copy with independent memory).

    This performs a full deep copy of the array, allocating new memory and copying
    all elements. The cloned array has its own heap allocation and can be safely
    modified without affecting the original.

    Args:
        codegen: The LLVM codegen instance.
        array_struct: The array struct value {len, cap, data*} to clone.
        element_type: The semantic element type of the array.

    Returns:
        A new array struct value with cloned data.

    Note:
        Empty arrays (len=0) return {0, 0, null} without allocating memory.
    """
    zero = ir.Constant(codegen.types.i32, 0)

    # Extract fields from source array
    source_len = codegen.builder.extract_value(array_struct, 0)
    source_cap = codegen.builder.extract_value(array_struct, 1)
    source_data_ptr = codegen.builder.extract_value(array_struct, 2)

    # Get LLVM element type
    element_llvm_type = codegen.types.ll_type(element_type)
    array_struct_type = array_struct.type

    # Check if source array is empty (len == 0)
    len_is_zero = codegen.builder.icmp_unsigned('==', source_len, zero)

    empty_clone_bb = codegen.builder.append_basic_block('clone_empty')
    non_empty_clone_bb = codegen.builder.append_basic_block('clone_non_empty')
    clone_merge_bb = codegen.builder.append_basic_block('clone_merge')

    codegen.builder.cbranch(len_is_zero, empty_clone_bb, non_empty_clone_bb)

    # Empty clone path: return {0, 0, null}
    codegen.builder.position_at_end(empty_clone_bb)
    null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)
    empty_array = ir.Constant(array_struct_type, ir.Undefined)
    empty_array = codegen.builder.insert_value(empty_array, zero, 0)
    empty_array = codegen.builder.insert_value(empty_array, zero, 1)
    empty_array = codegen.builder.insert_value(empty_array, null_ptr, 2)
    codegen.builder.branch(clone_merge_bb)

    # Non-empty clone path: allocate and copy
    codegen.builder.position_at_end(non_empty_clone_bb)

    # Allocate new memory (capacity * sizeof(element))
    # Use centralized size calculation with semantic type
    sizeof_element_i32 = codegen.types.get_type_size_constant(element_type)
    cap_i64 = codegen.builder.zext(source_cap, codegen.types.i64)
    sizeof_element_i64 = codegen.builder.zext(sizeof_element_i32, codegen.types.i64)
    total_bytes = codegen.builder.mul(cap_i64, sizeof_element_i64)

    # Use our malloc wrapper with error checking
    new_data_ptr_i8 = emit_malloc(codegen, codegen.builder, total_bytes)
    new_data_ptr = codegen.builder.bitcast(new_data_ptr_i8, ir.PointerType(element_llvm_type))

    # Copy elements (manual loop for portability)
    copy_index = codegen.builder.alloca(codegen.types.i32, name="copy_idx")
    codegen.builder.store(zero, copy_index)

    copy_loop_head = codegen.builder.append_basic_block('copy_loop_head')
    copy_loop_body = codegen.builder.append_basic_block('copy_loop_body')
    copy_loop_exit = codegen.builder.append_basic_block('copy_loop_exit')

    codegen.builder.branch(copy_loop_head)

    # Loop head: check if index < len
    codegen.builder.position_at_end(copy_loop_head)
    idx = codegen.builder.load(copy_index)
    cond = codegen.builder.icmp_unsigned('<', idx, source_len)
    codegen.builder.cbranch(cond, copy_loop_body, copy_loop_exit)

    # Loop body: deep-copy element. An owning element (a string / nested array / owning
    # struct / enum with heap payload) must get its OWN buffers, else the clone and the
    # source share element buffers and both free them at scope exit (double-free on a
    # nested container). emit_value_clone is a runtime no-op for a non-owning element type,
    # and is recursion-safe for a self-referential element type (out-of-line clone fn).
    codegen.builder.position_at_end(copy_loop_body)
    src_elem_ptr = codegen.builder.gep(source_data_ptr, [idx])
    elem = codegen.builder.load(src_elem_ptr)
    elem = emit_value_clone(codegen, elem, element_type)
    dst_elem_ptr = codegen.builder.gep(new_data_ptr, [idx])
    codegen.builder.store(elem, dst_elem_ptr)

    # Increment index
    next_idx = codegen.builder.add(idx, ir.Constant(codegen.types.i32, 1))
    codegen.builder.store(next_idx, copy_index)
    codegen.builder.branch(copy_loop_head)

    # Loop exit: create new array struct
    codegen.builder.position_at_end(copy_loop_exit)
    new_array = ir.Constant(array_struct_type, ir.Undefined)
    new_array = codegen.builder.insert_value(new_array, source_len, 0)
    new_array = codegen.builder.insert_value(new_array, source_cap, 1)
    new_array = codegen.builder.insert_value(new_array, new_data_ptr, 2)
    codegen.builder.branch(clone_merge_bb)

    # Merge: phi node to select result
    codegen.builder.position_at_end(clone_merge_bb)
    result_phi = codegen.builder.phi(array_struct_type, name="cloned_array")
    result_phi.add_incoming(empty_array, empty_clone_bb)
    result_phi.add_incoming(new_array, copy_loop_exit)

    return result_phi


def deep_copy_if_owning_struct(codegen: 'LLVMCodegen', value: ir.Value, semantic_type: Type) -> ir.Value:
    """Return an independent deep copy of `value` if it is a heap-owning struct.

    A struct that owns heap memory (a dynamic-array `T[]` field, directly or nested)
    must get its own buffers whenever it is copied -- taken out of an array via
    `.get()`/indexing, or passed by value to a function -- so exactly one owner frees
    each allocation (#60). For any other type (primitives, strings, references, structs
    without owned buffers) the value is returned unchanged.

    Args:
        codegen: The LLVM codegen instance.
        value: The emitted value being copied.
        semantic_type: The value's semantic type (may be an UnknownType struct name).

    Returns:
        A deep copy with independent buffers, or `value` unchanged.
    """
    from sushi_lang.semantics.typesys import UnknownType, EnumType
    from sushi_lang.backend.destructors import needs_cleanup

    resolved = semantic_type
    if isinstance(resolved, UnknownType):
        # An element type may name a struct OR an enum; resolve against both tables.
        resolved = (codegen.struct_table.by_name.get(resolved.name)
                    or codegen.enum_table.by_name.get(resolved.name)
                    or resolved)

    if isinstance(resolved, StructType) and codegen.dynamic_arrays.struct_needs_cleanup(resolved):
        return deep_copy_struct(codegen, value, resolved)
    # An enum with an owning payload (e.g. a `string` variant, #147) taken out of a container
    # by value must also get an independent copy, else the extracted value aliases the
    # container's buffer and both free it at scope exit (double-free). Clone via the unified
    # deep-copy, which mirrors the enum destructor's payload free.
    if isinstance(resolved, EnumType) and needs_cleanup(resolved):
        return emit_value_clone(codegen, value, resolved)
    return value


def move_owning_arg_into_container(codegen: 'LLVMCodegen', arg_ast) -> None:
    """Mark a bare-Name owning local as moved when it is stored into a container.

    `container.push(x)` / `.insert(x)` / `map.insert(k, x)` stores the value SHALLOWLY --
    the container's slot aliases the same heap buffer(s) as the source. Ownership therefore
    transfers to the container (which frees the value on `.destroy()`/`.free()`/scope exit),
    and the source local must be moved so scope-exit RAII does not ALSO free the shared
    buffer (double-free). Covers every owning local kind: strings (#145), dynamic arrays,
    `List<T>`, `Own<T>`, and heap-owning structs (#140). Only a bare Name of a
    currently-registered owning local is moved; a literal / temporary / method result is a
    fresh value the container simply takes over, with no source to move.
    """
    from sushi_lang.semantics.ast import Name
    if not isinstance(arg_ast, Name):
        return
    name = arg_ast.id
    da = codegen.dynamic_arrays
    if (codegen.memory.is_string_registered(name)
            or codegen.memory.is_struct_registered(name)
            or name in da.arrays
            or name in da.lists
            or name in da.owned_pointers):
        codegen.memory.mark_struct_as_moved(name)


def deep_copy_struct(codegen: 'LLVMCodegen', struct_value: ir.Value, struct_type: StructType) -> ir.Value:
    """Deep copy a struct value so it owns independent heap buffers.

    Delegates to the unified `emit_value_clone` (via `_clone_struct_value`), which clones
    exactly the fields `emit_value_destructor` would free -- dynamic-array, string (#147),
    nested-struct, enum, List and Own -- gated on the same `needs_cleanup` predicate. This
    replaces the former array-and-nested-struct-only field walk, so a struct string field
    now gets its own buffer on every copy path (no double-free with the scope-exit free).
    """
    return emit_value_clone(codegen, struct_value, struct_type)


def emit_value_clone(codegen: 'LLVMCodegen', value: ir.Value, value_type: Type) -> ir.Value:
    """Return a deep copy of `value` that owns independent heap buffers.

    Exact structural inverse of `destructors.emit_value_destructor`: it duplicates
    precisely the set of heap buffers that destructor would free for the same
    `value_type`. Clone fewer buffers -> double-free; clone more -> leak. It is a
    no-op passthrough for non-owning shapes, so callers invoke it unconditionally,
    mirroring how the free site calls the destructor unconditionally on the value.

    Value-in / value-out SSA (takes and returns the value, not a pointer), so it
    composes with a freshly loaded value such as a HashMap entry (#140).

    Args:
        codegen: The LLVM codegen instance.
        value: The emitted SSA value to clone.
        value_type: The value's semantic type (may be an UnknownType struct name).

    Returns:
        A deep copy with independent buffers, or `value` unchanged when non-owning.
    """
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

    # Foreign ptr / function values: passthrough. A capturing closure's heap env
    # cannot be generically duplicated (capture is erased from the type), so a
    # closure HashMap value is an accepted, pre-existing gap -- identical to the
    # closure gap in array `.get()`.
    if isinstance(value_type, (ForeignPtrType, FunctionType)):
        return value

    if isinstance(value_type, BuiltinType):
        if value_type == BuiltinType.STRING:
            return _clone_string_value(codegen, value)
        return value  # numerics, bool, I/O handles: nothing to clone

    if isinstance(value_type, (DynamicArrayType, StructType, EnumType)):
        return _emit_composite_clone(codegen, value, value_type)

    return value


def _clone_type_key(value_type: Type) -> str:
    """Stable identity key for a composite type's deep clone (mirrors the destructor key)."""
    if isinstance(value_type, DynamicArrayType):
        return "[]" + _clone_type_key(value_type.base_type)
    if isinstance(value_type, (StructType, EnumType)):
        return value_type.name
    return getattr(value_type, "name", type(value_type).__name__)


def _clone_symbol(value_type: Type) -> str:
    """Deterministic, symbol-safe name for a per-type clone function (linkonce_odr)."""
    mapping = {"<": "_L", ">": "_G", ",": "_C", "[": "_A", "]": "_E", " ": ""}
    out = []
    for ch in _clone_type_key(value_type):
        out.append(ch if (ch.isalnum() or ch == "_") else mapping.get(ch, "_"))
    return "__sushi_clone_" + "".join(out)


def _inline_clone(codegen: 'LLVMCodegen', value: ir.Value, value_type: Type) -> ir.Value:
    """Inline deep-clone dispatch for a composite type (no recursion guard)."""
    if isinstance(value_type, DynamicArrayType):
        return clone_dynamic_array_value(codegen, value, value_type.base_type)
    if isinstance(value_type, StructType):
        if value_type.name.startswith("Own<"):
            return _clone_own_value(codegen, value, value_type)
        if value_type.name.startswith("List<"):
            return _clone_list_value(codegen, value, value_type)
        return _clone_struct_value(codegen, value, value_type)
    if isinstance(value_type, EnumType):
        return _clone_enum_value(codegen, value, value_type)
    raise AssertionError(f"not a composite clone type: {value_type!r}")


def _emit_composite_clone(codegen: 'LLVMCodegen', value: ir.Value, value_type: Type) -> ir.Value:
    """Deep-clone a composite type, breaking self-referential cycles.

    Structural inverse of ``destructors._emit_composite_destructor``: a non-recursive type
    is cloned inline (unchanged behaviour), but when cloning re-enters a type already in
    progress (a self-referential type such as ``enum MsgValue: Arr(MsgValue[])`` or
    ``Own<Tree>``), an out-of-line per-type clone function is called at that position so the
    deep copy recurses at runtime over the actual data and terminates -- instead of
    recursing unbounded at compile time.
    """
    key = _clone_type_key(value_type)
    stack = getattr(codegen, "_clone_inprogress", None)
    if stack is None:
        stack = []
        codegen._clone_inprogress = stack

    if key in stack:
        fn = _get_or_emit_clone_func(codegen, value_type)
        return codegen.builder.call(fn, [value])

    stack.append(key)
    try:
        return _inline_clone(codegen, value, value_type)
    finally:
        stack.pop()


def _get_or_emit_clone_func(codegen: 'LLVMCodegen', value_type: Type) -> ir.Function:
    """Get (or lazily emit) the out-of-line deep-clone function for a recursive type.

    Signature is ``<T> __sushi_clone_<mangled>(<T> value)``. Inserted into the cache before
    its body is emitted, so a self-referential position inside the body resolves to a call
    to this same function. The inline clone helpers use ``codegen.builder``, so it is swapped
    to the new function's builder while the body is emitted, then restored.
    """
    key = _clone_type_key(value_type)
    funcs = getattr(codegen, "_clone_funcs", None)
    if funcs is None:
        funcs = {}
        codegen._clone_funcs = funcs
    if key in funcs:
        return funcs[key]

    lltype = codegen.types.ll_type(value_type)
    fn_ty = ir.FunctionType(lltype, [lltype])
    fn = ir.Function(codegen.module, fn_ty, name=_clone_symbol(value_type))
    fn.linkage = "linkonce_odr"
    funcs[key] = fn

    entry = fn.append_basic_block(name="entry")
    fb = ir.IRBuilder(entry)
    saved_builder = codegen.builder
    saved_stack = getattr(codegen, "_clone_inprogress", None)
    codegen.builder = fb
    codegen._clone_inprogress = [key]
    try:
        result = _inline_clone(codegen, fn.args[0], value_type)
        codegen.builder.ret(result)
    finally:
        codegen.builder = saved_builder
        codegen._clone_inprogress = saved_stack if saved_stack is not None else []
    return fn


def _declare_memcpy(codegen: 'LLVMCodegen'):
    """Declare the i64-length llvm.memcpy intrinsic (safe on ARM64, see #149)."""
    i8_ptr = ir.PointerType(codegen.types.i8)
    return codegen.module.declare_intrinsic(
        'llvm.memcpy', [i8_ptr, i8_ptr, ir.IntType(INT64_BIT_WIDTH)]
    )


def _clone_string_value(codegen: 'LLVMCodegen', fat: ir.Value) -> ir.Value:
    """Deep-copy a string's heap buffer, mirroring the owned-bit guard of the destructor.

    Fat layout is `{i8* data@0, i32 size@1, i8 owned@2}`. If `owned != 0` the buffer is
    heap-owned: malloc a fresh copy (i64-length memcpy -- the raw i32 size is unsafe on
    ARM64, #149) and set owned=1. A literal/borrow (owned=0) passes through unchanged, so
    exactly one owner frees each buffer -- symmetric with `emit_string_destructor`.
    """
    b = codegen.builder
    owned = b.extract_value(fat, 2, name="clone_str_owned")
    size = b.extract_value(fat, 1, name="clone_str_size")
    data = b.extract_value(fat, 0, name="clone_str_data")

    slot = b.alloca(fat.type, name="clone_str_slot")
    b.store(fat, slot)  # default: return input unchanged (owned == 0)

    is_owned = b.icmp_unsigned("!=", owned, ir.Constant(owned.type, 0))
    with b.if_then(is_owned):
        size_i64 = b.zext(size, ir.IntType(INT64_BIT_WIDTH))
        new_data = emit_malloc(codegen, codegen.builder, size_i64)  # i8*
        b.call(_declare_memcpy(codegen),
               [new_data, data, size_i64, ir.Constant(ir.IntType(1), 0)])
        cloned = b.insert_value(fat, new_data, 0)
        cloned = b.insert_value(cloned, ir.Constant(codegen.types.i8, 1), 2)
        b.store(cloned, slot)

    return b.load(slot, name="cloned_string")


def _clone_own_value(codegen: 'LLVMCodegen', value: ir.Value, value_type: StructType) -> ir.Value:
    """Deep-copy an Own<T>: mirror the destructor's Own path (recurse pointee, own ptr).

    Own<T> is `{T* value@0}`. Null-guard the pointer, recursively clone the pointee (so
    nested Own<Own<T>> descends), malloc a fresh pointee slot, and store the clone -- the
    returned Own owns an independent allocation that its destructor frees exactly once.
    """
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
    """Deep-copy a List<T>: allocate a fresh buffer and copy the elements.

    List<T> is `{i32 len@0, i32 cap@1, T* data@2}`. Null-data passes through unchanged
    (empty list). Otherwise malloc cap*sizeof(T) and memcpy the `len` live elements
    (shallow per element -- one level, matching `clone_dynamic_array_value`). The new
    buffer is freed exactly once by the symmetric List branch added to the destructor.
    """
    from sushi_lang.backend.generics.list.types import extract_element_type

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
        b.store(b.insert_value(value, new_data, 2), slot)

    return b.load(slot, name="cloned_list")


def _clone_struct_value(codegen: 'LLVMCodegen', value: ir.Value, value_type: StructType) -> ir.Value:
    """Deep-copy a regular struct field-by-field, recursing through emit_value_clone.

    Gated on `field_needs_cleanup(field_type)` -- the SAME predicate `_emit_struct_destructor`
    uses -- so exactly the fields the destructor frees get cloned, at full depth (a struct
    holding an enum/List/Own field is handled, unlike `deep_copy_struct` which only covers
    array and nested-struct fields; that helper's other call sites are left untouched).
    The gate must RESOLVE a named/generic field type, exactly as the destructor does: clone
    fewer buffers than the destructor frees and the shared buffer is freed twice (#183).
    """
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
    """Deep-copy an enum by cloning the active variant's owning associated data.

    Mirrors `_emit_enum_destructor`: switch on the tag and walk the same byte offsets into
    the `[N x i8]` data blob -- but CLONE each owning field in place (load, clone, store
    back) instead of destroying. Materialised through an alloca so the byte-offset GEPs
    have an address; the mutated value is reloaded as a single dominating SSA result.
    """
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

        offset = 0
        for assoc_type in variant.associated_types:
            if field_needs_cleanup(codegen, assoc_type):
                data_i8_ptr = b.bitcast(data_ptr, ir.PointerType(ir.IntType(8)),
                                        name="clone_enum_data_i8")
                field_i8_ptr = b.gep(data_i8_ptr, [make_i32_const(offset)],
                                     name="clone_enum_field_i8")
                field_llvm = codegen.types.ll_type(assoc_type)
                field_ptr = b.bitcast(field_i8_ptr, ir.PointerType(field_llvm),
                                      name="clone_enum_field_ptr")
                orig = b.load(field_ptr, name="clone_enum_orig")
                b.store(emit_value_clone(codegen, orig, assoc_type), field_ptr)
            offset += codegen.types.get_type_size_bytes(assoc_type)

        b.branch(end_bb)

    b.position_at_end(end_bb)
    return b.load(slot, name="cloned_enum")
