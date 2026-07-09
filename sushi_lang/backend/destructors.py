"""
Unified value destruction logic for all Sushi types.

This module provides the canonical implementation of recursive cleanup for:
- Dynamic arrays (with recursive element cleanup)
- Structs (with recursive field cleanup)
- Enums (with variant-based cleanup)
- Own<T> (heap-allocated owned values)

This code was previously duplicated in both memory_manager.py and llvm_memory.py.
Now both modules delegate to these functions for consistency.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import llvmlite.ir as ir

from sushi_lang.semantics.typesys import Type, BuiltinType, DynamicArrayType, StructType, EnumType, FunctionType
from sushi_lang.backend.constants import INT8_BIT_WIDTH, DA_DATA_INDEX
from sushi_lang.backend.llvm_constants import ZERO_I32, ONE_I32, make_i32_const

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_value_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value,
    value_type: Type
) -> None:
    """Recursively destroy a value of any type.

    This is the central cleanup mechanism for all Sushi types:
    - Primitives (i8-i64, u8-u64, f32, f64, bool): no-op
    - Strings: no-op (string literals or static data)
    - Dynamic arrays: free data pointer, recursively destroy elements if needed
    - Structs: recursively destroy each field
    - Enums: switch on discriminant tag, destroy variant data
    - Own<T>: free owned pointer, recursively destroy owned value

    Args:
        codegen: The main codegen instance (for accessing types, free, etc.)
        builder: The LLVM IR builder to emit code with
        value_ptr: Pointer to the value to destroy (not the value itself)
        value_type: The Sushi type of the value
    """
    # Strings: runtime-guarded free of the heap buffer via the owned bit (#145). A literal /
    # borrow carries owned=0, making the free a no-op -- same data-driven discipline as the
    # closure drop_ptr (ownership can't be told from the uniform `string` type alone).
    if isinstance(value_type, BuiltinType):
        if value_type == BuiltinType.STRING:
            emit_string_destructor(codegen, builder, value_ptr)
            return
        if value_type in (BuiltinType.STDIN, BuiltinType.STDOUT,
                          BuiltinType.STDERR, BuiltinType.FILE):
            # I/O handle types don't need cleanup
            return
        # All numeric types and bool: no cleanup
        return

    # Composite owning types (dynamic arrays, structs/List/Own, enums): routed through
    # the recursion-safe wrapper. It inlines the destructor for a non-recursive type
    # (zero behaviour change) but, when a type transitively contains itself (e.g.
    # `enum MsgValue: Arr(MsgValue[])` or `Own<Tree>`), emits an out-of-line per-type
    # destructor function and calls it at the self-referential position, so cleanup
    # terminates via runtime recursion instead of unbounded compile-time inlining (#139).
    elif isinstance(value_type, (DynamicArrayType, StructType, EnumType)):
        _emit_composite_destructor(codegen, builder, value_ptr, value_type)

    # Function values (closures): free the heap environment through the runtime-guarded
    # drop pointer. Capture is erased from the type, so ownership is resolved at runtime:
    # a non-capturing value carries drop_ptr = null, making the free a no-op.
    elif isinstance(value_type, FunctionType):
        emit_function_value_destructor(codegen, builder, value_ptr)


def _select_inline_destructor(value_type: Type):
    """Pick the inline destructor emitter for a composite type."""
    if isinstance(value_type, DynamicArrayType):
        return _emit_dynamic_array_destructor
    if isinstance(value_type, StructType):
        return _emit_struct_destructor
    if isinstance(value_type, EnumType):
        return _emit_enum_destructor
    raise AssertionError(f"not a composite destructor type: {value_type!r}")


def _dtor_type_key(value_type: Type) -> str:
    """A stable identity key for a composite type's destructor.

    Two occurrences of the same type share a key (so a self-referential type is
    detected on re-entry, and its out-of-line destructor is emitted once).
    """
    if isinstance(value_type, DynamicArrayType):
        return "[]" + _dtor_type_key(value_type.base_type)
    if isinstance(value_type, (StructType, EnumType)):
        return value_type.name
    return getattr(value_type, "name", type(value_type).__name__)


def _dtor_symbol(value_type: Type) -> str:
    """Deterministic, symbol-safe name for a per-type destructor function.

    Deterministic across compilation units so the `linkonce_odr` bodies emitted in
    different units for the same recursive type deduplicate at link time.
    """
    mapping = {"<": "_L", ">": "_G", ",": "_C", "[": "_A", "]": "_E", " ": ""}
    out = []
    for ch in _dtor_type_key(value_type):
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append(mapping.get(ch, "_"))
    return "__sushi_dtor_" + "".join(out)


def _emit_composite_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value,
    value_type: Type,
) -> None:
    """Emit a composite type's destructor, breaking self-referential cycles.

    A non-recursive type is inlined exactly as before. When emission re-enters a
    type already on the in-progress stack (a self-referential type such as
    `enum MsgValue: Arr(MsgValue[])` or `Own<Tree>`), a call to an out-of-line
    per-type destructor is emitted at that position instead of inlining the body
    again -- so the cleanup recurses at runtime over the actual data and terminates,
    rather than recursing unbounded at compile time (the original #139 crash).
    """
    key = _dtor_type_key(value_type)
    stack = getattr(codegen, "_dtor_inprogress", None)
    if stack is None:
        stack = []
        codegen._dtor_inprogress = stack

    if key in stack:
        fn = _get_or_emit_dtor_func(codegen, value_type)
        i8_ptr = builder.bitcast(value_ptr, ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
        builder.call(fn, [i8_ptr])
        return

    stack.append(key)
    try:
        _select_inline_destructor(value_type)(codegen, builder, value_ptr, value_type)
    finally:
        stack.pop()


def _get_or_emit_dtor_func(codegen: LLVMCodegen, value_type: Type) -> ir.Function:
    """Get (or lazily emit) the out-of-line destructor function for a recursive type.

    Signature is `void __sushi_dtor_<mangled>(i8* value_ptr)`. The function is inserted
    into the cache BEFORE its body is emitted, so a self-referential position inside the
    body resolves to a call to this same function (terminating the emission). The body is
    built with a fresh in-progress stack seeded with this type's key, so the recursion
    point becomes a self-call while unrelated nested types still inline.
    """
    key = _dtor_type_key(value_type)
    funcs = getattr(codegen, "_dtor_funcs", None)
    if funcs is None:
        funcs = {}
        codegen._dtor_funcs = funcs
    if key in funcs:
        return funcs[key]

    i8_ptr_ty = ir.PointerType(ir.IntType(INT8_BIT_WIDTH))
    fn_ty = ir.FunctionType(ir.VoidType(), [i8_ptr_ty])
    fn = ir.Function(codegen.module, fn_ty, name=_dtor_symbol(value_type))
    fn.linkage = "linkonce_odr"
    funcs[key] = fn

    entry = fn.append_basic_block(name="entry")
    fb = ir.IRBuilder(entry)
    typed_ptr = fb.bitcast(
        fn.args[0],
        ir.PointerType(codegen.types.ll_type(value_type)),
        name="self_ptr",
    )

    saved = getattr(codegen, "_dtor_inprogress", None)
    codegen._dtor_inprogress = [key]
    try:
        _select_inline_destructor(value_type)(codegen, fb, typed_ptr, value_type)
    finally:
        codegen._dtor_inprogress = saved if saved is not None else []
    fb.ret_void()
    return fn


def emit_function_value_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value
) -> None:
    """Free a closure's heap environment via its type-erased drop pointer.

    `value_ptr` points at the `{i8* fn_ptr, i8* env_ptr, i8* drop_ptr}` fat value. The
    free is `if (drop_ptr != null) drop_ptr(env_ptr)` -- a non-capturing function value
    stores a null drop, so this is a guarded no-op for it (the whole point of the
    data-driven drop slot: ownership cannot be told from the `fn(...)` type alone).
    """
    fat = builder.load(value_ptr, name="closure_val")
    emit_function_value_destructor_from_value(codegen, builder, fat)


def emit_function_value_destructor_from_value(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    fat: ir.Value
) -> None:
    """Free a closure's heap environment given the SSA fat value directly.

    Same guarded `if (drop_ptr != null) drop_ptr(env_ptr)` as
    emit_function_value_destructor, but operates on an already-materialised
    `{i8* fn_ptr, i8* env_ptr, i8* drop_ptr}` value rather than loading it from a
    slot. Used to free an unnamed inline-closure argument temporary (#123), which has
    no alloca -- only the SSA fat value produced by emit_lambda. The value is produced
    before any branch, so it dominates every early-exit block.
    """
    drop_ptr = builder.extract_value(fat, 2, name="closure_drop")
    env_ptr = builder.extract_value(fat, 1, name="closure_env")

    is_not_null = builder.icmp_unsigned(
        "!=", drop_ptr, ir.Constant(drop_ptr.type, None)
    )
    with builder.if_then(is_not_null):
        drop_fn_ty = ir.FunctionType(ir.VoidType(), [codegen.types.str_ptr])
        drop_callee = builder.bitcast(drop_ptr, ir.PointerType(drop_fn_ty), name="closure_drop_fn")
        builder.call(drop_callee, [env_ptr])


def emit_string_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value
) -> None:
    """Runtime-guarded free of a string's heap buffer via the owned bit (#145).

    `value_ptr` points at the `{i8* data, i32 size, i8 owned}` fat pointer. The free is
    `if (owned != 0) free(data)` -- a literal/borrow carries owned=0, so this is a guarded
    no-op for it. This makes strings inside structs / arrays / List / HashMap / enum
    variants free correctly through the ordinary recursive destructor, with no per-container
    special-casing (the bit travels with the value).
    """
    fat = builder.load(value_ptr, name="string_val")
    emit_string_destructor_from_value(codegen, builder, fat)


def emit_string_destructor_from_value(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    fat: ir.Value
) -> None:
    """Owned-bit-guarded free given the SSA fat value directly (`if owned: free(data)`) (#145).

    Used to free an unnamed fresh string temporary that has no alloca -- e.g. an
    interpolation intermediate (a to-string or intermediate-concat buffer) consumed by the
    next concat. A borrowed part (owned bit set but aliasing another owner) must NOT be
    passed here; only genuinely fresh temporaries.
    """
    owned = builder.extract_value(fat, 2, name="string_owned")
    is_owned = builder.icmp_unsigned("!=", owned, ir.Constant(owned.type, 0))
    with builder.if_then(is_owned):
        data_ptr = builder.extract_value(fat, 0, name="string_data")
        free_fn = codegen.get_free_func()
        builder.call(free_fn, [data_ptr])


def _emit_dynamic_array_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value,
    value_type: DynamicArrayType
) -> None:
    """Emit destructor code for a dynamic array.

    Frees the data pointer and recursively destroys elements if needed.
    """
    # Load the dynamic array struct
    data_ptr_ptr = builder.gep(value_ptr, [
        ZERO_I32,
        make_i32_const(DA_DATA_INDEX)
    ], name="array_data_ptr")
    data_ptr = builder.load(data_ptr_ptr, name="array_data")

    # Check if data is not null before freeing
    is_not_null = builder.icmp_unsigned(
        "!=", data_ptr,
        ir.Constant(data_ptr.type, None)
    )

    with builder.if_then(is_not_null):
        # Check if element type needs cleanup
        if needs_cleanup(value_type.base_type):
            # Load array length
            len_ptr = builder.gep(value_ptr, [
                ZERO_I32,
                ZERO_I32  # len is first field
            ], name="array_len_ptr")
            array_len = builder.load(len_ptr, name="array_len")

            # Iterate through array elements and destroy each one
            loop_i = builder.alloca(ZERO_I32.type, name="cleanup_i")
            builder.store(ZERO_I32, loop_i)

            loop_cond_bb = builder.append_basic_block(name="array_cleanup_cond")
            loop_body_bb = builder.append_basic_block(name="array_cleanup_body")
            loop_end_bb = builder.append_basic_block(name="array_cleanup_end")

            builder.branch(loop_cond_bb)

            # Loop condition: i < len
            builder.position_at_end(loop_cond_bb)
            i_val = builder.load(loop_i, name="i_val")
            cond = builder.icmp_unsigned("<", i_val, array_len, name="cleanup_cond")
            builder.cbranch(cond, loop_body_bb, loop_end_bb)

            # Loop body: destroy element[i]
            builder.position_at_end(loop_body_bb)
            i_val = builder.load(loop_i, name="i_val")
            element_ptr = builder.gep(data_ptr, [i_val], name="element_ptr")

            # Recursively destroy this element
            emit_value_destructor(codegen, builder, element_ptr, value_type.base_type)

            # Increment loop counter
            i_next = builder.add(i_val, ONE_I32, name="i_next")
            builder.store(i_next, loop_i)
            builder.branch(loop_cond_bb)

            # After loop, free the array data
            builder.position_at_end(loop_end_bb)

        # Free the array data pointer
        void_ptr = builder.bitcast(data_ptr, ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
        free_func = codegen.get_free_func()
        builder.call(free_func, [void_ptr])


def _emit_struct_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value,
    value_type: StructType
) -> None:
    """Emit destructor code for a struct.

    Handles Own<T> specially, otherwise recursively destroys each field.
    """
    # Check if this is Own<T> which needs special handling
    if value_type.name.startswith("Own<"):
        # Own<T> has a single pointer field - free it and destroy the value
        ptr_field_ptr = builder.gep(value_ptr, [
            ZERO_I32,
            ZERO_I32
        ], name="own_ptr_field")
        owned_ptr = builder.load(ptr_field_ptr, name="owned_ptr")

        # Check if not null
        is_not_null = builder.icmp_unsigned(
            "!=", owned_ptr,
            ir.Constant(owned_ptr.type, None)
        )

        with builder.if_then(is_not_null):
            # Get the owned element type T from Own<T>. NOTE: fields[0][1] is the raw
            # POINTER type (T*), not T - recursing with it would be a silent no-op and
            # leak the payload of a nested Own<Own<T>>. Use the pointee element type so
            # the recursion actually descends (owned_ptr already IS the T* address).
            if value_type.fields:
                from sushi_lang.backend.generics.own import get_own_element_type
                owned_type = get_own_element_type(value_type)
                # Recursively destroy the owned value
                emit_value_destructor(codegen, builder, owned_ptr, owned_type)

            # Free the pointer itself
            void_ptr = builder.bitcast(owned_ptr, ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
            free_func = codegen.get_free_func()
            builder.call(free_func, [void_ptr])
    elif value_type.name.startswith("List<"):
        # List<T> owns a single heap buffer at field 2 (data), like a dynamic array but
        # with a raw T* rather than a DynamicArrayType field -- so the generic field loop
        # below would free nothing. Destroy live elements then free the buffer, keeping
        # this in lockstep with _clone_list_value (issue #140).
        _emit_list_value_destructor(codegen, builder, value_ptr, value_type)
    else:
        # Regular struct: recursively destroy each field
        for i, (field_name, field_type) in enumerate(value_type.fields):
            # Check if field needs cleanup
            if needs_cleanup(field_type):
                field_ptr = builder.gep(value_ptr, [
                    ZERO_I32,
                    make_i32_const(i)
                ], name=f"field_{field_name}_ptr")
                emit_value_destructor(codegen, builder, field_ptr, field_type)


def _emit_list_value_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value,
    value_type: StructType
) -> None:
    """Free a List<T> value's heap buffer, destroying live elements first.

    List<T> is `{i32 len@0, i32 cap@1, T* data@2}`. This mirrors the dynamic-array
    destructor (null-guard, per-element recursive cleanup when the element needs it,
    then free the buffer) so a List stored as a HashMap value -- reached through the
    generic emit_value_destructor rather than the by-name scope destructor -- is freed
    exactly once, symmetric with _clone_list_value (issue #140).
    """
    from sushi_lang.backend.generics.list.types import extract_element_type

    element_type = extract_element_type(value_type, codegen)

    # data is field index 2 (DA_DATA_INDEX == 2 happens to match List's data slot)
    data_ptr_ptr = builder.gep(value_ptr, [ZERO_I32, make_i32_const(2)], name="list_data_field")
    data_ptr = builder.load(data_ptr_ptr, name="list_data")

    is_not_null = builder.icmp_unsigned("!=", data_ptr, ir.Constant(data_ptr.type, None))
    with builder.if_then(is_not_null):
        if needs_cleanup(element_type):
            len_ptr = builder.gep(value_ptr, [ZERO_I32, ZERO_I32], name="list_len_field")
            list_len = builder.load(len_ptr, name="list_len")

            loop_i = builder.alloca(ZERO_I32.type, name="list_cleanup_i")
            builder.store(ZERO_I32, loop_i)

            cond_bb = builder.append_basic_block(name="list_cleanup_cond")
            body_bb = builder.append_basic_block(name="list_cleanup_body")
            done_bb = builder.append_basic_block(name="list_cleanup_done")

            builder.branch(cond_bb)
            builder.position_at_end(cond_bb)
            i_val = builder.load(loop_i, name="list_i")
            cond = builder.icmp_unsigned("<", i_val, list_len, name="list_cleanup_cond_v")
            builder.cbranch(cond, body_bb, done_bb)

            builder.position_at_end(body_bb)
            i_val = builder.load(loop_i, name="list_i")
            element_ptr = builder.gep(data_ptr, [i_val], name="list_element_ptr")
            emit_value_destructor(codegen, builder, element_ptr, element_type)
            builder.store(builder.add(i_val, ONE_I32), loop_i)
            builder.branch(cond_bb)

            builder.position_at_end(done_bb)

        void_ptr = builder.bitcast(data_ptr, ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
        free_func = codegen.get_free_func()
        builder.call(free_func, [void_ptr])


def _emit_enum_destructor(
    codegen: LLVMCodegen,
    builder: ir.IRBuilder,
    value_ptr: ir.Value,
    value_type: EnumType
) -> None:
    """Emit destructor code for an enum.

    Creates a switch statement to handle cleanup for each variant with associated data.
    """
    # Load discriminant tag (first field of enum struct)
    tag_ptr = builder.gep(value_ptr, [ZERO_I32, ZERO_I32], name="enum_tag_ptr")
    tag = builder.load(tag_ptr, name="enum_tag")

    # Get data field pointer (second field: [N x i8] byte array)
    data_ptr = builder.gep(value_ptr, [ZERO_I32, ONE_I32], name="enum_data_ptr")

    # Create switch statement for each variant
    # We need to check which variants have associated data that needs cleanup
    variants_needing_cleanup = []
    for i, variant in enumerate(value_type.variants):
        if variant.associated_types:
            # Check if any associated type needs cleanup
            if any(needs_cleanup(assoc_type) for assoc_type in variant.associated_types):
                variants_needing_cleanup.append((i, variant))

    if variants_needing_cleanup:
        # Create basic blocks for each variant that needs cleanup
        cleanup_blocks = {}
        for tag_val, variant in variants_needing_cleanup:
            cleanup_blocks[tag_val] = builder.append_basic_block(name=f"cleanup_variant_{variant.name}")

        end_block = builder.append_basic_block(name="enum_cleanup_end")

        # Create switch instruction
        switch = builder.switch(tag, end_block)

        # Add cases for each variant that needs cleanup
        for tag_val, variant in variants_needing_cleanup:
            tag_const = make_i32_const(tag_val)
            switch.add_case(tag_const, cleanup_blocks[tag_val])

        # Emit cleanup code for each variant
        for tag_val, variant in variants_needing_cleanup:
            builder.position_at_end(cleanup_blocks[tag_val])

            # Calculate offset into data array for each associated value
            offset = 0
            for j, assoc_type in enumerate(variant.associated_types):
                if needs_cleanup(assoc_type):
                    # Get pointer to this field within the data array
                    # Cast the [N x i8]* to i8* first
                    data_i8_ptr = builder.bitcast(data_ptr, ir.PointerType(ir.IntType(8)), name=f"data_i8_ptr_{j}")

                    # Add offset to get to this field
                    offset_const = make_i32_const(offset)
                    field_i8_ptr = builder.gep(data_i8_ptr, [offset_const], name=f"field_{j}_i8_ptr")

                    # Cast to the actual field type pointer
                    field_llvm_type = codegen.types.ll_type(assoc_type)
                    field_ptr = builder.bitcast(field_i8_ptr, ir.PointerType(field_llvm_type), name=f"field_{j}_ptr")

                    # Recursively destroy this field
                    emit_value_destructor(codegen, builder, field_ptr, assoc_type)

                # Update offset for next field
                offset += codegen.types.get_type_size_bytes(assoc_type)

            builder.branch(end_block)

        # Position at end block for continuation
        builder.position_at_end(end_block)


def resolve_named_type(codegen: LLVMCodegen, value_type: Type) -> Type:
    """Resolve an `UnknownType` name against the struct and enum tables.

    A named type may be a struct OR an enum. `needs_cleanup` and
    `emit_value_destructor` both dispatch on the resolved class, so an unresolved
    name silently reports "no cleanup needed" and destroys nothing.
    """
    from sushi_lang.semantics.typesys import UnknownType
    if not isinstance(value_type, UnknownType):
        return value_type
    return (codegen.struct_table.by_name.get(value_type.name)
            or codegen.enum_table.by_name.get(value_type.name)
            or value_type)


def needs_cleanup(value_type: Type) -> bool:
    """Check if a type needs cleanup (has resources to free).

    Named types must be resolved (see `resolve_named_type`) before calling this:
    a bare `UnknownType` falls through to False.

    Args:
        value_type: The type to check

    Returns:
        True if the type needs cleanup, False otherwise
    """
    from sushi_lang.semantics.typesys import ForeignPtrType
    if isinstance(value_type, ForeignPtrType):
        return False  # Foreign `ptr` is unmanaged: RAII never frees it
    if isinstance(value_type, FunctionType):
        # A function value may own a heap environment (a capturing closure). Capture is
        # erased from the type, so we conservatively treat every function value as
        # needing cleanup; the runtime-guarded drop makes a non-capturing free a no-op.
        return True
    if isinstance(value_type, BuiltinType):
        # A `string` owns a heap buffer when its runtime `owned` bit is set (#145/#147); the
        # destructor's free is guarded on that bit, so a literal/borrow frees to a no-op. This
        # is what lets strings inside structs / arrays / List / HashMap / enum payloads free
        # through the ordinary recursive destructor. All other builtins (numerics, bool, I/O
        # handles) are unmanaged.
        return value_type == BuiltinType.STRING
    elif isinstance(value_type, DynamicArrayType):
        return True  # Dynamic arrays need cleanup
    elif isinstance(value_type, StructType):
        # Structs need cleanup if any field needs cleanup
        return any(needs_cleanup(field_type) for _, field_type in value_type.fields)
    elif isinstance(value_type, EnumType):
        # Enums need cleanup if any variant has associated data that needs cleanup
        for variant in value_type.variants:
            if variant.associated_types:
                if any(needs_cleanup(assoc_type) for assoc_type in variant.associated_types):
                    return True
        return False
    return False
