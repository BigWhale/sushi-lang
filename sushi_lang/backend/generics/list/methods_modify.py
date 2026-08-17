"""List<T> modification methods: push(), pop(), get(), clear(), insert(), remove()."""

from typing import Any
from sushi_lang.semantics.typesys import StructType
import llvmlite.ir as ir

from .types import get_list_len_ptr, get_list_capacity_ptr, get_list_element_type, extract_element_type, get_list_data_ptr
from sushi_lang.backend.constants.llvm_values import FALSE_I1


def emit_list_push(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.push(element) - append element with auto-growth."""
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend import gep_utils

    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(capacity_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    two = ir.Constant(codegen.types.i32, 2)

    need_growth = codegen.builder.icmp_unsigned(">=", current_len, current_cap)

    before_if = codegen.builder.block

    with codegen.builder.if_then(need_growth):

        cap_is_zero = codegen.builder.icmp_unsigned("==", current_cap, zero)
        double_cap = codegen.builder.mul(current_cap, two)
        new_cap = codegen.builder.select(cap_is_zero, one, double_cap, name="new_cap")

        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        new_total_size = codegen.builder.mul(new_cap, element_size, name="new_total_size")

        new_data_ptr = memory.emit_realloc_call(codegen, data_ptr, new_total_size)
        typed_new_data_ptr = codegen.builder.bitcast(
            new_data_ptr,
            ir.PointerType(element_llvm_type),
            name="typed_new_data_ptr"
        )

        codegen.builder.store(new_cap, capacity_ptr)
        codegen.builder.store(typed_new_data_ptr, data_ptr_ptr)
        after_if = codegen.builder.block

    phi = codegen.builder.phi(data_ptr.type, name="data_ptr_phi")
    phi.add_incoming(data_ptr, before_if)
    if 'after_if' in locals():
        phi.add_incoming(typed_new_data_ptr, after_if)
    data_ptr = phi

    # Evaluate element to push. The list stores it shallowly and frees it on
    # `.destroy()`/scope exit, so this is a consuming use: the seam decides whether the
    # source hands ownership over, detaches with a copy, or is rejected outright.
    from sushi_lang.backend.ownership import ConsumingUse, consume
    element_value = codegen.expressions.emit_expr(expr.args[0])
    element_value = consume(codegen, expr.args[0], element_value, element_type,
                            ConsumingUse.CONTAINER_INSERT)

    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, current_len, "element_ptr")
    codegen.builder.store(element_value, element_ptr)

    new_len = codegen.builder.add(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    return codegen.builder.load(list_alloca, name="updated_list")


def emit_list_pop(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.pop() - remove and return last element."""
    from sushi_lang.backend import gep_utils

    element_type = extract_element_type(list_type, codegen)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    zero = ir.Constant(codegen.types.i32, 0)
    is_empty = codegen.builder.icmp_unsigned("==", current_len, zero)

    empty_block = codegen.func.append_basic_block("pop_empty")
    not_empty_block = codegen.func.append_basic_block("pop_not_empty")
    end_block = codegen.func.append_basic_block("pop_end")

    codegen.builder.cbranch(is_empty, empty_block, not_empty_block)

    codegen.builder.position_at_end(empty_block)
    from sushi_lang.backend.generics import maybe
    none_value = maybe.emit_maybe_none(codegen, element_type)
    codegen.builder.branch(end_block)
    empty_predecessor = codegen.builder.block

    codegen.builder.position_at_end(not_empty_block)

    one = ir.Constant(codegen.types.i32, 1)
    new_len = codegen.builder.sub(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, new_len, "element_ptr")
    element_value = codegen.builder.load(element_ptr, name="element")

    some_value = maybe.emit_maybe_some(codegen, element_type, element_value)
    codegen.builder.branch(end_block)
    not_empty_predecessor = codegen.builder.block

    codegen.builder.position_at_end(end_block)
    maybe_type = maybe.get_maybe_enum_type(codegen, element_type)
    phi = codegen.builder.phi(maybe_type, name="pop_result")
    phi.add_incoming(none_value, empty_predecessor)
    phi.add_incoming(some_value, not_empty_predecessor)

    return phi


def emit_list_get(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.get(index) - safe element access."""
    from sushi_lang.backend import gep_utils
    from sushi_lang.backend.generics import maybe

    element_type = extract_element_type(list_type, codegen)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    index_value = codegen.expressions.emit_expr(expr.args[0])

    zero = ir.Constant(codegen.types.i32, 0)
    index_not_negative = codegen.builder.icmp_signed(">=", index_value, zero)
    index_in_bounds = codegen.builder.icmp_unsigned("<", index_value, current_len)
    bounds_ok = codegen.builder.and_(index_not_negative, index_in_bounds, name="bounds_ok")

    in_bounds_block = codegen.func.append_basic_block("get_in_bounds")
    out_of_bounds_block = codegen.func.append_basic_block("get_out_of_bounds")
    end_block = codegen.func.append_basic_block("get_end")

    codegen.builder.cbranch(bounds_ok, in_bounds_block, out_of_bounds_block)

    codegen.builder.position_at_end(out_of_bounds_block)
    none_value = maybe.emit_maybe_none(codegen, element_type)
    codegen.builder.branch(end_block)
    out_of_bounds_predecessor = codegen.builder.block

    codegen.builder.position_at_end(in_bounds_block)
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")
    element_value = codegen.builder.load(element_ptr, name="element")

    # `.get()` READS. It does not detach (#242): `get` does NOT remove the element, so the
    # list keeps it and still frees it at scope exit, and the returned `Maybe.Some(T)`
    # carries a BORROW. Pass 3 classifies it BORROWED, a `let` of it binds without owning,
    # and a position that takes ownership rejects it (CE2411).
    #
    # `emit_list_pop` is the opposite and stays that way: pop decrements `len`, so the
    # popped element falls outside the destructor's `data[0..len)` walk and the list no
    # longer owns it. That Maybe is FRESH and MOVES the element.
    some_value = maybe.emit_maybe_some(codegen, element_type, element_value)
    codegen.builder.branch(end_block)
    in_bounds_predecessor = codegen.builder.block

    codegen.builder.position_at_end(end_block)
    maybe_type = maybe.get_maybe_enum_type(codegen, element_type)
    phi = codegen.builder.phi(maybe_type, name="get_result")
    phi.add_incoming(none_value, out_of_bounds_predecessor)
    phi.add_incoming(some_value, in_bounds_predecessor)

    return phi


def emit_list_clear(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.clear() - remove all elements but keep capacity."""
    element_type = extract_element_type(list_type, codegen)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    _emit_destroy_elements_loop(codegen, data_ptr, current_len, element_type)

    zero = ir.Constant(codegen.types.i32, 0)
    codegen.builder.store(zero, len_ptr)

    return codegen.builder.load(list_alloca, name="cleared_list")


def _emit_destroy_elements_loop(codegen: Any, data_ptr: ir.Value, count: ir.Value, element_type: Any) -> None:
    """Destroy every element in data[0..count) with the recursive destructor."""
    from sushi_lang.backend.destructors import emit_value_destructor
    from sushi_lang.backend.generics.container_walk import emit_container_walk

    def destroy(element_ptr: ir.Value, _index: ir.Value) -> None:
        emit_value_destructor(codegen, element_ptr, element_type)

    emit_container_walk(codegen, data_ptr, count, destroy, prefix="destroy")


def emit_list_insert(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.insert(index, element) - insert element at position."""
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend import gep_utils

    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(capacity_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    index_value = codegen.expressions.emit_expr(expr.args[0])

    # Bounds check: 0 <= index <= len (note: len is valid for append-like insert)
    zero = ir.Constant(codegen.types.i32, 0)
    index_not_negative = codegen.builder.icmp_signed(">=", index_value, zero)
    index_valid = codegen.builder.icmp_unsigned("<=", index_value, current_len)
    bounds_ok = codegen.builder.and_(index_not_negative, index_valid, name="bounds_ok")

    in_bounds_block = codegen.func.append_basic_block("insert_in_bounds")
    out_of_bounds_block = codegen.func.append_basic_block("insert_out_of_bounds")
    end_block = codegen.func.append_basic_block("insert_end")

    codegen.builder.cbranch(bounds_ok, in_bounds_block, out_of_bounds_block)

    codegen.builder.position_at_end(out_of_bounds_block)
    from sushi_lang.semantics.typesys import BuiltinType
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
    std_error = codegen.enum_table.by_name.get("StdError")
    result_type = ensure_result_type_in_table(codegen.enum_table, BuiltinType.BLANK, std_error, struct_table=codegen.struct_table.by_name)
    result_llvm_type = codegen.types.ll_type(result_type)
    err_enum = ir.Constant(result_llvm_type, ir.Undefined)
    err_enum = codegen.builder.insert_value(err_enum, ir.Constant(codegen.types.i32, 1), 0, name="Result_Err_tag")
    err_block = codegen.builder.block
    codegen.builder.branch(end_block)

    codegen.builder.position_at_end(in_bounds_block)

    need_growth = codegen.builder.icmp_unsigned(">=", current_len, current_cap)

    before_growth = codegen.builder.block

    with codegen.builder.if_then(need_growth):
        one = ir.Constant(codegen.types.i32, 1)
        two = ir.Constant(codegen.types.i32, 2)

        cap_is_zero = codegen.builder.icmp_unsigned("==", current_cap, zero)
        double_cap = codegen.builder.mul(current_cap, two)
        new_cap = codegen.builder.select(cap_is_zero, one, double_cap, name="new_cap")

        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        new_total_size = codegen.builder.mul(new_cap, element_size, name="new_total_size")

        new_data_ptr = memory.emit_realloc_call(codegen, data_ptr, new_total_size)
        typed_new_data_ptr = codegen.builder.bitcast(
            new_data_ptr,
            ir.PointerType(element_llvm_type),
            name="typed_new_data_ptr"
        )

        codegen.builder.store(new_cap, capacity_ptr)
        codegen.builder.store(typed_new_data_ptr, data_ptr_ptr)
        after_growth = codegen.builder.block

    phi = codegen.builder.phi(data_ptr.type, name="data_ptr_phi")
    phi.add_incoming(data_ptr, before_growth)
    if 'after_growth' in locals():
        phi.add_incoming(typed_new_data_ptr, after_growth)
    data_ptr = phi

    # Now shift elements from [index, len) one position to the right
    # We need to move (len - index) elements
    # Use llvm.memmove for overlapping memory regions
    num_to_move = codegen.builder.sub(current_len, index_value, name="num_to_move")

    has_elements_to_shift = codegen.builder.icmp_unsigned(">", num_to_move, zero)

    with codegen.builder.if_then(has_elements_to_shift):
        src_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "src_ptr")

        one = ir.Constant(codegen.types.i32, 1)
        index_plus_one = codegen.builder.add(index_value, one, name="index_plus_one")
        dest_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_plus_one, "dest_ptr")

        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        bytes_to_move = codegen.builder.mul(num_to_move, element_size, name="bytes_to_move")

        src_i8 = codegen.builder.bitcast(src_ptr, ir.PointerType(codegen.types.i8))
        dest_i8 = codegen.builder.bitcast(dest_ptr, ir.PointerType(codegen.types.i8))

        # Call llvm.memmove intrinsic. i64-length form + zero-extended byte count so the
        # runtime i32 length cannot leak garbage upper bits into the length register that
        # glibc's memmove reads on x86-64 (#149/#151).
        memmove_fn = codegen.module.declare_intrinsic(
            'llvm.memmove',
            [ir.PointerType(codegen.types.i8), ir.PointerType(codegen.types.i8), codegen.types.i64]
        )
        is_volatile = FALSE_I1
        bytes_to_move_i64 = codegen.builder.zext(bytes_to_move, codegen.types.i64)
        codegen.builder.call(memmove_fn, [dest_i8, src_i8, bytes_to_move_i64, is_volatile])

    from sushi_lang.backend.ownership import ConsumingUse, consume
    element_value = codegen.expressions.emit_expr(expr.args[1])
    element_value = consume(codegen, expr.args[1], element_value, element_type,
                            ConsumingUse.CONTAINER_INSERT)

    insert_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "insert_ptr")
    codegen.builder.store(element_value, insert_ptr)

    one = ir.Constant(codegen.types.i32, 1)
    new_len = codegen.builder.add(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    ok_enum = ir.Constant(result_llvm_type, ir.Undefined)
    ok_enum = codegen.builder.insert_value(ok_enum, ir.Constant(codegen.types.i32, 0), 0, name="Result_Ok_tag")
    data_array_type = result_llvm_type.elements[1]
    temp_alloca = codegen.builder.alloca(data_array_type, name="enum_data_temp")
    data_ptr = codegen.builder.bitcast(temp_alloca, ir.PointerType(codegen.types.i8), name="data_ptr")
    arg_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(codegen.types.i32), name="arg0_ptr_typed")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), arg_ptr)
    packed_data = codegen.builder.load(temp_alloca, name="packed_data")
    ok_enum = codegen.builder.insert_value(ok_enum, packed_data, 1, name="Result_Ok_data")
    ok_block = codegen.builder.block
    codegen.builder.branch(end_block)

    codegen.builder.position_at_end(end_block)
    result_phi = codegen.builder.phi(ok_enum.type, name="insert_result")
    result_phi.add_incoming(err_enum, err_block)
    result_phi.add_incoming(ok_enum, ok_block)

    return result_phi


def emit_list_remove(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.remove(index) - remove element at position."""
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend import gep_utils
    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table

    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    index_value = codegen.expressions.emit_expr(expr.args[0])

    zero = ir.Constant(codegen.types.i32, 0)
    index_not_negative = codegen.builder.icmp_signed(">=", index_value, zero)
    index_in_bounds = codegen.builder.icmp_unsigned("<", index_value, current_len)
    bounds_ok = codegen.builder.and_(index_not_negative, index_in_bounds, name="bounds_ok")

    in_bounds_block = codegen.func.append_basic_block("remove_in_bounds")
    out_of_bounds_block = codegen.func.append_basic_block("remove_out_of_bounds")
    end_block = codegen.func.append_basic_block("remove_end")

    codegen.builder.cbranch(bounds_ok, in_bounds_block, out_of_bounds_block)

    codegen.builder.position_at_end(out_of_bounds_block)
    maybe_type = ensure_maybe_type_in_table(codegen.enum_table, element_type, struct_table=codegen.struct_table.by_name)
    maybe_llvm_type = codegen.types.ll_type(maybe_type)
    none_enum = ir.Constant(maybe_llvm_type, ir.Undefined)
    none_enum = codegen.builder.insert_value(none_enum, ir.Constant(codegen.types.i32, 1), 0, name="Maybe_None_tag")
    none_block = codegen.builder.block
    codegen.builder.branch(end_block)

    codegen.builder.position_at_end(in_bounds_block)

    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")
    element_value = codegen.builder.load(element_ptr, name="removed_element")

    one = ir.Constant(codegen.types.i32, 1)
    num_to_move = codegen.builder.sub(current_len, index_value, name="num_after_index")
    num_to_move = codegen.builder.sub(num_to_move, one, name="num_to_move")

    has_elements_to_shift = codegen.builder.icmp_unsigned(">", num_to_move, zero)

    with codegen.builder.if_then(has_elements_to_shift):
        index_plus_one = codegen.builder.add(index_value, one, name="index_plus_one")
        src_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_plus_one, "src_ptr")

        dest_ptr = element_ptr

        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        bytes_to_move = codegen.builder.mul(num_to_move, element_size, name="bytes_to_move")

        src_i8 = codegen.builder.bitcast(src_ptr, ir.PointerType(codegen.types.i8))
        dest_i8 = codegen.builder.bitcast(dest_ptr, ir.PointerType(codegen.types.i8))

        # Call llvm.memmove intrinsic to shift left. i64-length form + zero-extended byte
        # count so the runtime i32 length cannot leak garbage upper bits (#149/#151).
        memmove_fn = codegen.module.declare_intrinsic(
            'llvm.memmove',
            [ir.PointerType(codegen.types.i8), ir.PointerType(codegen.types.i8), codegen.types.i64]
        )
        is_volatile = FALSE_I1
        bytes_to_move_i64 = codegen.builder.zext(bytes_to_move, codegen.types.i64)
        codegen.builder.call(memmove_fn, [dest_i8, src_i8, bytes_to_move_i64, is_volatile])

    new_len = codegen.builder.sub(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    some_enum = ir.Constant(maybe_llvm_type, ir.Undefined)
    some_enum = codegen.builder.insert_value(some_enum, ir.Constant(codegen.types.i32, 0), 0, name="Maybe_Some_tag")

    data_array_type = maybe_llvm_type.elements[1]
    temp_alloca = codegen.builder.alloca(data_array_type, name="enum_data_temp")
    data_ptr_enum = codegen.builder.bitcast(temp_alloca, ir.PointerType(codegen.types.i8), name="data_ptr")

    arg_ptr = codegen.builder.bitcast(data_ptr_enum, ir.PointerType(element_llvm_type), name="arg0_ptr_typed")
    codegen.builder.store(element_value, arg_ptr)

    packed_data = codegen.builder.load(temp_alloca, name="packed_data")
    some_enum = codegen.builder.insert_value(some_enum, packed_data, 1, name="Maybe_Some_data")
    some_block = codegen.builder.block
    codegen.builder.branch(end_block)

    codegen.builder.position_at_end(end_block)
    result_phi = codegen.builder.phi(some_enum.type, name="remove_result")
    result_phi.add_incoming(none_enum, none_block)
    result_phi.add_incoming(some_enum, some_block)

    return result_phi
