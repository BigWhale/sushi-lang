"""Unified value destruction logic for all Sushi types.

Every emitter here reads the AMBIENT `codegen.builder` rather than taking one: an
out-of-line destructor body swaps it for its own function, and reading it is what keeps a
body and the loop helpers it calls emitting into the SAME function (#257).
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import llvmlite.ir as ir

from sushi_lang.semantics.typesys import (
    Type, BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType, FunctionType)
from sushi_lang.backend.constants import INT8_BIT_WIDTH, DA_DATA_INDEX
from sushi_lang.backend.constants.llvm_values import ZERO_I32, ONE_I32, make_i32_const

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_value_destructor(
    codegen: LLVMCodegen,
    value_ptr: ir.Value,
    value_type: Type
) -> None:
    """Recursively destroy a value of any type."""
    # Resolve a named / generic reference to the concrete struct/enum it names, so the
    # dispatch below lands on a real class. A Result is an ordinary interned enum.
    value_type = resolve_named_type(codegen, value_type)

    # Strings: runtime-guarded free of the heap buffer via the owned bit (#145). A literal /
    # borrow carries owned=0, making the free a no-op -- same data-driven discipline as the
    # closure drop_ptr (ownership can't be told from the uniform `string` type alone).
    if isinstance(value_type, BuiltinType):
        if value_type == BuiltinType.STRING:
            emit_string_destructor(codegen, value_ptr)
            return
        return

    # Through the recursion-safe wrapper: it inlines for a non-recursive type, but a
    # self-referential one gets an out-of-line per-type function, so cleanup terminates
    # by runtime recursion instead of unbounded compile-time inlining (#139).
    elif isinstance(value_type, (DynamicArrayType, ArrayType, StructType, EnumType)):
        _emit_composite_destructor(codegen, value_ptr, value_type)

    # Function values (closures): free the heap environment through the runtime-guarded
    # drop pointer. Capture is erased from the type, so ownership is resolved at runtime:
    # a non-capturing value carries drop_ptr = null, making the free a no-op.
    elif isinstance(value_type, FunctionType):
        emit_function_value_destructor(codegen, value_ptr)


def _emit_composite_destructor(
    codegen: LLVMCodegen,
    value_ptr: ir.Value,
    value_type: Type,
) -> None:
    """Emit a composite type's destructor, breaking self-referential cycles."""
    from sushi_lang.backend import lifecycle
    builder = codegen.builder
    key = lifecycle.composite_type_key(value_type)
    stack = codegen._dtor_inprogress

    if key in stack:
        fn = lifecycle.get_or_emit_lifecycle_func(codegen, value_type, "destroy")
        i8_ptr = builder.bitcast(value_ptr, ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
        builder.call(fn, [i8_ptr])
        return

    stack.append(key)
    try:
        lifecycle.inline_destroy(codegen, value_ptr, value_type)
    finally:
        stack.pop()


def emit_function_value_destructor(
    codegen: LLVMCodegen,
    value_ptr: ir.Value
) -> None:
    """Free a closure's heap environment via its type-erased drop pointer."""
    builder = codegen.builder
    fat = builder.load(value_ptr, name="closure_val")
    emit_function_value_destructor_from_value(codegen, fat)


def emit_function_value_destructor_from_value(
    codegen: LLVMCodegen,
    fat: ir.Value
) -> None:
    """Free a closure's heap environment given the SSA fat value directly."""
    builder = codegen.builder
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
    value_ptr: ir.Value
) -> None:
    """Runtime-guarded free of a string's heap buffer via the owned bit (#145)."""
    builder = codegen.builder
    fat = builder.load(value_ptr, name="string_val")
    emit_string_destructor_from_value(codegen, fat)


def emit_string_destructor_from_value(
    codegen: LLVMCodegen,
    fat: ir.Value
) -> None:
    """Owned-bit-guarded free given the SSA fat value directly (`if owned: free(data)`) (#145).
    """
    builder = codegen.builder
    owned = builder.extract_value(fat, 2, name="string_owned")
    is_owned = builder.icmp_unsigned("!=", owned, ir.Constant(owned.type, 0))
    with builder.if_then(is_owned):
        data_ptr = builder.extract_value(fat, 0, name="string_data")
        free_fn = codegen.get_free_func()
        builder.call(free_fn, [data_ptr])


def _emit_dynamic_array_destructor(
    codegen: LLVMCodegen,
    value_ptr: ir.Value,
    value_type: DynamicArrayType
) -> None:
    """Emit destructor code for a dynamic array."""
    # Load the dynamic array struct
    builder = codegen.builder
    data_ptr_ptr = builder.gep(value_ptr, [
        ZERO_I32,
        make_i32_const(DA_DATA_INDEX)
    ], name="array_data_ptr")
    data_ptr = builder.load(data_ptr_ptr, name="array_data")

    is_not_null = builder.icmp_unsigned(
        "!=", data_ptr,
        ir.Constant(data_ptr.type, None)
    )

    with builder.if_then(is_not_null):
        # Check if element type needs cleanup (resolving a named/generic element first --
        # an unresolved name answers False and the elements are silently leaked)
        if needs_cleanup(codegen, value_type.base_type):
            len_ptr = builder.gep(value_ptr, [
                ZERO_I32,
                ZERO_I32  # len is first field
            ], name="array_len_ptr")
            array_len = builder.load(len_ptr, name="array_len")

            loop_i = builder.alloca(ZERO_I32.type, name="cleanup_i")
            builder.store(ZERO_I32, loop_i)

            loop_cond_bb = builder.append_basic_block(name="array_cleanup_cond")
            loop_body_bb = builder.append_basic_block(name="array_cleanup_body")
            loop_end_bb = builder.append_basic_block(name="array_cleanup_end")

            builder.branch(loop_cond_bb)

            builder.position_at_end(loop_cond_bb)
            i_val = builder.load(loop_i, name="i_val")
            cond = builder.icmp_unsigned("<", i_val, array_len, name="cleanup_cond")
            builder.cbranch(cond, loop_body_bb, loop_end_bb)

            builder.position_at_end(loop_body_bb)
            i_val = builder.load(loop_i, name="i_val")
            element_ptr = builder.gep(data_ptr, [i_val], name="element_ptr")

            emit_value_destructor(codegen, element_ptr, value_type.base_type)

            i_next = builder.add(i_val, ONE_I32, name="i_next")
            builder.store(i_next, loop_i)
            builder.branch(loop_cond_bb)

            builder.position_at_end(loop_end_bb)

        void_ptr = builder.bitcast(data_ptr, ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
        free_func = codegen.get_free_func()
        builder.call(free_func, [void_ptr])


def _emit_fixed_array_destructor(
    codegen: LLVMCodegen,
    value_ptr: ir.Value,
    value_type: 'ArrayType'
) -> None:
    """Emit destructor code for a fixed-size array `T[N]` (#185)."""
    builder = codegen.builder
    if not needs_cleanup(codegen, value_type.base_type):
        return

    count = ir.Constant(ZERO_I32.type, value_type.size)
    first_elem = builder.gep(value_ptr, [ZERO_I32, ZERO_I32], name="fixed_first_elem")

    loop_i = builder.alloca(ZERO_I32.type, name="fixed_cleanup_i")
    builder.store(ZERO_I32, loop_i)

    cond_bb = builder.append_basic_block(name="fixed_cleanup_cond")
    body_bb = builder.append_basic_block(name="fixed_cleanup_body")
    end_bb = builder.append_basic_block(name="fixed_cleanup_end")

    builder.branch(cond_bb)

    builder.position_at_end(cond_bb)
    i_val = builder.load(loop_i, name="i_val")
    cond = builder.icmp_unsigned("<", i_val, count, name="fixed_cleanup_cond")
    builder.cbranch(cond, body_bb, end_bb)

    builder.position_at_end(body_bb)
    i_val = builder.load(loop_i, name="i_val")
    element_ptr = builder.gep(first_elem, [i_val], name="fixed_element_ptr")
    emit_value_destructor(codegen, element_ptr, value_type.base_type)
    builder.store(builder.add(i_val, ONE_I32, name="i_next"), loop_i)
    builder.branch(cond_bb)

    builder.position_at_end(end_bb)


def _emit_struct_destructor(
    codegen: LLVMCodegen,
    value_ptr: ir.Value,
    value_type: StructType
) -> None:
    """Emit destructor code for a struct."""
    # Check if this is Own<T> which needs special handling
    builder = codegen.builder
    if value_type.name.startswith("Own<"):
        ptr_field_ptr = builder.gep(value_ptr, [
            ZERO_I32,
            ZERO_I32
        ], name="own_ptr_field")
        owned_ptr = builder.load(ptr_field_ptr, name="owned_ptr")

        is_not_null = builder.icmp_unsigned(
            "!=", owned_ptr,
            ir.Constant(owned_ptr.type, None)
        )

        with builder.if_then(is_not_null):
            # `fields[0][1]` is the raw POINTER type (T*), not T: recursing with it is a
            # silent no-op that leaks a nested `Own<Own<T>>`. Use the pointee.
            if value_type.fields:
                from sushi_lang.semantics.generics.own import get_own_element_type
                owned_type = get_own_element_type(value_type)
                emit_value_destructor(codegen, owned_ptr, owned_type)

            void_ptr = builder.bitcast(owned_ptr, ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
            free_func = codegen.get_free_func()
            builder.call(free_func, [void_ptr])
    elif value_type.name.startswith("List<"):
        # List<T>'s data field is a raw T*, not a DynamicArrayType, so the generic field
        # loop below frees nothing. Keep in lockstep with _clone_list_value (#140).
        _emit_list_value_destructor(codegen, value_ptr, value_type)
    elif value_type.name.startswith("HashMap<"):
        # The owning keys/values live in an LLVM-only Entry<K, V> buffer the generic field
        # loop cannot see -- `buckets` is an i32[] placeholder. In lockstep with
        # _clone_hashmap_value (#181).
        _emit_hashmap_value_destructor(codegen, value_ptr, value_type)
    else:
        for i, (field_name, field_type) in enumerate(value_type.fields):
            if needs_cleanup(codegen, field_type):
                field_ptr = builder.gep(value_ptr, [
                    ZERO_I32,
                    make_i32_const(i)
                ], name=f"field_{field_name}_ptr")
                emit_value_destructor(codegen, field_ptr, field_type)


def _emit_list_value_destructor(
    codegen: LLVMCodegen,
    value_ptr: ir.Value,
    value_type: StructType
) -> None:
    """Free a List<T> value's heap buffer, destroying live elements first."""
    builder = codegen.builder
    from sushi_lang.backend.generics.list.types import extract_element_type

    element_type = extract_element_type(value_type, codegen)

    data_ptr_ptr = builder.gep(value_ptr, [ZERO_I32, make_i32_const(2)], name="list_data_field")
    data_ptr = builder.load(data_ptr_ptr, name="list_data")

    is_not_null = builder.icmp_unsigned("!=", data_ptr, ir.Constant(data_ptr.type, None))
    with builder.if_then(is_not_null):
        if needs_cleanup(codegen, element_type):
            from sushi_lang.backend.generics.container_walk import emit_container_walk

            len_ptr = builder.gep(value_ptr, [ZERO_I32, ZERO_I32], name="list_len_field")
            list_len = builder.load(len_ptr, name="list_len")

            def destroy_element(element_ptr: ir.Value, _index: ir.Value) -> None:
                emit_value_destructor(codegen, element_ptr, element_type)

            emit_container_walk(codegen, data_ptr, list_len, destroy_element,
                                prefix="list_cleanup")

        void_ptr = builder.bitcast(data_ptr, ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
        free_func = codegen.get_free_func()
        builder.call(free_func, [void_ptr])


def _emit_hashmap_value_destructor(
    codegen: LLVMCodegen,
    value_ptr: ir.Value,
    value_type: StructType
) -> None:
    """Free a HashMap<K, V> value's bucket buffer, destroying every occupied Entry first."""
    builder = codegen.builder
    from sushi_lang.backend.generics.hashmap.types import (
        get_hashmap_field_ptrs, get_entry_type
    )
    from sushi_lang.backend.generics.hashmap.utils import emit_destroy_all_entries
    from sushi_lang.semantics.generics.hashmap import extract_key_value_types

    key_type, value_type_kv = extract_key_value_types(value_type, codegen)

    fields = get_hashmap_field_ptrs(codegen, value_ptr)
    capacity = builder.load(fields.capacity, name="hm_dtor_cap")
    buckets_data = builder.load(fields.buckets_data, name="hm_dtor_data")

    emit_destroy_all_entries(codegen, buckets_data, capacity, key_type, value_type_kv,
                             null_guard=True)

    entry_llvm = get_entry_type(codegen, key_type, value_type_kv)
    is_not_null = builder.icmp_unsigned(
        "!=", buckets_data, ir.Constant(ir.PointerType(entry_llvm), None))
    with builder.if_then(is_not_null):
        void_ptr = builder.bitcast(buckets_data,
                                   ir.PointerType(ir.IntType(INT8_BIT_WIDTH)))
        free_func = codegen.get_free_func()
        builder.call(free_func, [void_ptr])


def _emit_enum_destructor(
    codegen: LLVMCodegen,
    value_ptr: ir.Value,
    value_type: EnumType
) -> None:
    """Emit destructor code for an enum."""
    # Load discriminant tag (first field of enum struct)
    builder = codegen.builder
    tag_ptr = builder.gep(value_ptr, [ZERO_I32, ZERO_I32], name="enum_tag_ptr")
    tag = builder.load(tag_ptr, name="enum_tag")

    data_ptr = builder.gep(value_ptr, [ZERO_I32, ONE_I32], name="enum_data_ptr")

    variants_needing_cleanup = []
    for i, variant in enumerate(value_type.variants):
        if variant.associated_types:
            # Resolve named payloads FIRST: `needs_cleanup()` answers False for an
            # unresolved name, which silently dropped the variant from the switch and
            # leaked its heap (#179).
            resolved_types = tuple(
                resolve_named_type(codegen, assoc_type)
                for assoc_type in variant.associated_types)
            if any(needs_cleanup(codegen, assoc_type) for assoc_type in resolved_types):
                variants_needing_cleanup.append((i, variant, resolved_types))

    if variants_needing_cleanup:
        cleanup_blocks = {}
        for tag_val, variant, _resolved in variants_needing_cleanup:
            cleanup_blocks[tag_val] = builder.append_basic_block(name=f"cleanup_variant_{variant.name}")

        end_block = builder.append_basic_block(name="enum_cleanup_end")

        switch = builder.switch(tag, end_block)

        for tag_val, _variant, _resolved in variants_needing_cleanup:
            tag_const = make_i32_const(tag_val)
            switch.add_case(tag_const, cleanup_blocks[tag_val])

        for tag_val, _variant, resolved_types in variants_needing_cleanup:
            builder.position_at_end(cleanup_blocks[tag_val])

            # Field offsets from the ONE layout authority (#300 phase 2), so the
            # destructor reads exactly where construction wrote.
            field_offsets = codegen.types.payload_field_offsets(resolved_types)
            for j, (assoc_type, field_offset) in enumerate(zip(resolved_types, field_offsets, strict=True)):
                if needs_cleanup(codegen, assoc_type):
                    data_i8_ptr = builder.bitcast(data_ptr, ir.PointerType(ir.IntType(8)), name=f"data_i8_ptr_{j}")

                    offset_const = make_i32_const(field_offset)
                    field_i8_ptr = builder.gep(data_i8_ptr, [offset_const], name=f"field_{j}_i8_ptr")

                    field_llvm_type = codegen.types.ll_type(assoc_type)
                    field_ptr = builder.bitcast(field_i8_ptr, ir.PointerType(field_llvm_type), name=f"field_{j}_ptr")

                    emit_value_destructor(codegen, field_ptr, assoc_type)

            builder.branch(end_block)

        builder.position_at_end(end_block)


def resolve_named_type(codegen: LLVMCodegen, value_type: Type) -> Type:
    """Resolve a named or generic type reference against the struct and enum tables."""
    from sushi_lang.semantics.typesys import UnknownType
    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(value_type, UnknownType):
        name = value_type.name
    elif isinstance(value_type, GenericTypeRef):
        name = str(value_type)
    elif isinstance(value_type, ArrayType):
        # Resolve THROUGH a fixed array to its element: `needs_cleanup` is table-free, so
        # an unresolved element makes the whole array look like it owns nothing (#185).
        base = resolve_named_type(codegen, value_type.base_type)
        return value_type if base is value_type.base_type else ArrayType(base, value_type.size)
    else:
        return value_type
    return (codegen.struct_table.by_name.get(name)
            or codegen.enum_table.by_name.get(name)
            or value_type)


def emit_declared_drop(codegen: LLVMCodegen, value_ptr: ir.Value,
                       value_type: Type) -> None:
    """Call a type's own `drop()`, if it declared one (HANDLES.md ruling R2).

    The ORDER is the contract: the type's own `drop()` runs FIRST, then its owning
    fields are destroyed. A handle is still readable while its owner closes itself
    down, which is what a `drop()` that flushes a buffer into its inner handle needs.

    The receiver is `poke self`, so it arrives by pointer and `value_ptr` is already
    the right shape. The method was emitted through the extension path, so its symbol
    is the extension symbol -- one authority for the name, shared with the declaration
    and every ordinary call site.
    """
    name = getattr(value_type, "name", None)
    impls = getattr(codegen, "perk_impl_table", None)
    if name is None or impls is None or not impls.implements(name, "Drop"):
        return

    from sushi_lang.semantics.generics.name_mangling import extension_symbol
    fn = codegen.funcs.lookup(extension_symbol(name, "drop"), codegen.emitting_unit)
    if fn is None:
        return
    codegen.builder.call(fn, [value_ptr])


def emit_struct_fields_except(codegen: LLVMCodegen, value_ptr: ir.Value,
                              value_type: Type, keep_field: str) -> None:
    """Destroy every owning field of a struct but one -- the marked field take (R28).

    `drop()` is deliberately NOT called, and that is the ruling rather than an omission:
    a destructor is written for a value that goes away WHOLE, and here one field
    survives it, so a `drop()` that flushed into the taken handle or closed it would be
    told it still owns what the caller is taking. The method performing the take does
    the finishing work itself, which is what `into_inner()` spells.

    Field order matches `_emit_struct_destructor`'s, because it is the same walk with
    one slot held back.
    """
    resolved = resolve_named_type(codegen, value_type)
    if not isinstance(resolved, StructType):
        return
    builder = codegen.builder
    for i, (field_name, field_type) in enumerate(resolved.fields):
        if field_name == keep_field or not needs_cleanup(codegen, field_type):
            continue
        field_ptr = builder.gep(value_ptr, [ZERO_I32, make_i32_const(i)],
                                name=f"take_rest_{field_name}_ptr")
        emit_value_destructor(codegen, field_ptr, field_type)


def needs_cleanup(codegen: LLVMCodegen, value_type: Type) -> bool:
    """Does a value of this type own something RAII must release?

    ONE backend cleanup predicate (ruling R2a). It used to be two -- a table-free
    `needs_cleanup` and a resolving `field_needs_cleanup` -- and a third walk in
    `memory/dynamic_arrays.py` answered the same question a third way. One gated
    RECURSION and another gated REGISTRATION, and the two disagreeing is exactly what
    #162 and #183 were.

    `codegen` is required and comes first, per the backend rule. It carries both
    answers the predicate cannot work without: the tables that resolve a named type
    (the "resolve first" obligation that #179 and #185 were paid for) and the set of
    types that implement `Drop`.
    """
    from sushi_lang.semantics.typesys import owns_resource
    from sushi_lang.backend.ownership import drops_of
    return owns_resource(resolve_named_type(codegen, value_type), drops_of(codegen),
                         resolve=lambda t: resolve_named_type(codegen, t))


def destroy_old_value(codegen: LLVMCodegen, value_ptr: ir.Value, value_type: Type) -> None:
    """Free the value a store is about to overwrite, or the store leaks it.

    A plain type owns no heap, so this is a no-op there and the caller needs no gate.
    """
    resolved = resolve_named_type(codegen, value_type)
    if needs_cleanup(codegen, resolved):
        emit_value_destructor(codegen, value_ptr, resolved)


# The DESTROY half of every composite kind's handler; the CLONE half registers in
# backend/expressions/memory.py. A kind registered on one side only is a double free or a
# leak by construction, so tests/unit/test_lifecycle_handlers.py asserts the pairing.
from sushi_lang.backend.lifecycle import register_lifecycle as _register_lifecycle  # noqa: E402

_register_lifecycle("dynamic_array", destroy=_emit_dynamic_array_destructor)
_register_lifecycle("fixed_array", destroy=_emit_fixed_array_destructor)
_register_lifecycle("struct", destroy=_emit_struct_destructor)
_register_lifecycle("enum", destroy=_emit_enum_destructor)
