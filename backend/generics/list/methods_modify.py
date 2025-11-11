"""
List<T> modification methods: push(), pop(), get(), clear(), insert(), remove().

These methods modify the list contents or access elements.
"""

from typing import Any
from semantics.typesys import StructType
import llvmlite.ir as ir

from .types import get_list_len_ptr, get_list_capacity_ptr, get_list_element_type, extract_element_type
from backend.llvm_constants import LIST_DATA_INDICES, FALSE_I1


def emit_list_push(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.push(element) - append element with auto-growth.

    Implements 2x exponential growth strategy like T[]:
    1. Check if len >= capacity
    2. If true: realloc to 2x capacity (or 1 if currently 0)
    3. Store element at data[len]
    4. Increment len
    5. Mutates list in-place

    Args:
        codegen: LLVM codegen instance.
        expr: The method call expression.
        list_ptr: Pointer to the List<T> struct.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        Unit value (~).
    """
    from backend.expressions import memory
    from backend import gep_utils

    # Extract element type
    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    # Use the provided pointer directly
    list_alloca = list_ptr

    # Get pointers to fields
    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = codegen.builder.gep(
        list_alloca,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(capacity_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Constants
    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    two = ir.Constant(codegen.types.i32, 2)

    # Check if growth is needed
    need_growth = codegen.builder.icmp_unsigned(">=", current_len, current_cap)

    # Save block for phi node
    before_if = codegen.builder.block

    with codegen.builder.if_then(need_growth):
        # Calculate new capacity: cap == 0 ? 1 : cap * 2

        cap_is_zero = codegen.builder.icmp_unsigned("==", current_cap, zero)
        double_cap = codegen.builder.mul(current_cap, two)
        new_cap = codegen.builder.select(cap_is_zero, one, double_cap, name="new_cap")

        # Calculate new size: new_cap * sizeof(T)
        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        new_total_size = codegen.builder.mul(new_cap, element_size, name="new_total_size")

        # Reallocate
        new_data_ptr = memory.emit_realloc_call(codegen, data_ptr, new_total_size)
        typed_new_data_ptr = codegen.builder.bitcast(
            new_data_ptr,
            ir.PointerType(element_llvm_type),
            name="typed_new_data_ptr"
        )

        # Update capacity and data pointer
        codegen.builder.store(new_cap, capacity_ptr)
        codegen.builder.store(typed_new_data_ptr, data_ptr_ptr)
        after_if = codegen.builder.block

    # Phi node to merge data_ptr
    phi = codegen.builder.phi(data_ptr.type, name="data_ptr_phi")
    phi.add_incoming(data_ptr, before_if)
    if 'after_if' in locals():
        phi.add_incoming(typed_new_data_ptr, after_if)
    data_ptr = phi

    # Evaluate element to push
    element_value = codegen.expressions.emit_expr(expr.args[0])

    # Store element at data[len]
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, current_len, "element_ptr")
    codegen.builder.store(element_value, element_ptr)

    # Increment length
    new_len = codegen.builder.add(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    # Return updated list
    return codegen.builder.load(list_alloca, name="updated_list")


def emit_list_pop(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.pop() - remove and return last element.

    Returns Maybe<T>:
    - Maybe.Some(element) if list not empty
    - Maybe.None() if list is empty

    Args:
        codegen: LLVM codegen instance.
        list_ptr: Pointer to the List<T> struct.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        A Maybe<T> enum value.
    """
    from backend import gep_utils

    # Extract element type
    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    # Use the provided pointer directly
    list_alloca = list_ptr

    # Get pointers to fields
    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = codegen.builder.gep(
        list_alloca,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Check if empty
    zero = ir.Constant(codegen.types.i32, 0)
    is_empty = codegen.builder.icmp_unsigned("==", current_len, zero)

    # Create blocks
    empty_block = codegen.func.append_basic_block("pop_empty")
    not_empty_block = codegen.func.append_basic_block("pop_not_empty")
    end_block = codegen.func.append_basic_block("pop_end")

    codegen.builder.cbranch(is_empty, empty_block, not_empty_block)

    # Empty case: return Maybe.None()
    codegen.builder.position_at_end(empty_block)
    from backend.generics import maybe
    none_value = maybe.emit_maybe_none(codegen, element_type)
    codegen.builder.branch(end_block)
    empty_predecessor = codegen.builder.block

    # Not empty case: return Maybe.Some(element)
    codegen.builder.position_at_end(not_empty_block)

    # Decrement length
    one = ir.Constant(codegen.types.i32, 1)
    new_len = codegen.builder.sub(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    # Load element at data[new_len]
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, new_len, "element_ptr")
    element_value = codegen.builder.load(element_ptr, name="element")

    # Create Maybe.Some(element)
    some_value = maybe.emit_maybe_some(codegen, element_type, element_value)
    codegen.builder.branch(end_block)
    not_empty_predecessor = codegen.builder.block

    # Merge block
    codegen.builder.position_at_end(end_block)
    maybe_type = maybe.get_maybe_enum_type(codegen, element_type)
    phi = codegen.builder.phi(maybe_type, name="pop_result")
    phi.add_incoming(none_value, empty_predecessor)
    phi.add_incoming(some_value, not_empty_predecessor)

    return phi


def emit_list_get(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.get(index) - safe element access.

    Returns Maybe<T>:
    - Maybe.Some(element) if index is in bounds
    - Maybe.None() if index is out of bounds

    Args:
        codegen: LLVM codegen instance.
        expr: The method call expression.
        list_ptr: Pointer to the List<T> struct.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        A Maybe<T> enum value.
    """
    from backend import gep_utils
    from backend.generics import maybe

    # Extract element type
    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    # Use the provided pointer directly
    list_alloca = list_ptr

    # Get pointers to fields
    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = codegen.builder.gep(
        list_alloca,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Evaluate index
    index_value = codegen.expressions.emit_expr(expr.args[0])

    # Bounds check: 0 <= index < len
    zero = ir.Constant(codegen.types.i32, 0)
    index_not_negative = codegen.builder.icmp_signed(">=", index_value, zero)
    index_in_bounds = codegen.builder.icmp_unsigned("<", index_value, current_len)
    bounds_ok = codegen.builder.and_(index_not_negative, index_in_bounds, name="bounds_ok")

    # Create blocks
    in_bounds_block = codegen.func.append_basic_block("get_in_bounds")
    out_of_bounds_block = codegen.func.append_basic_block("get_out_of_bounds")
    end_block = codegen.func.append_basic_block("get_end")

    codegen.builder.cbranch(bounds_ok, in_bounds_block, out_of_bounds_block)

    # Out of bounds: return Maybe.None()
    codegen.builder.position_at_end(out_of_bounds_block)
    none_value = maybe.emit_maybe_none(codegen, element_type)
    codegen.builder.branch(end_block)
    out_of_bounds_predecessor = codegen.builder.block

    # In bounds: return Maybe.Some(element)
    codegen.builder.position_at_end(in_bounds_block)
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")
    element_value = codegen.builder.load(element_ptr, name="element")
    some_value = maybe.emit_maybe_some(codegen, element_type, element_value)
    codegen.builder.branch(end_block)
    in_bounds_predecessor = codegen.builder.block

    # Merge
    codegen.builder.position_at_end(end_block)
    maybe_type = maybe.get_maybe_enum_type(codegen, element_type)
    phi = codegen.builder.phi(maybe_type, name="get_result")
    phi.add_incoming(none_value, out_of_bounds_predecessor)
    phi.add_incoming(some_value, in_bounds_predecessor)

    return phi


def emit_list_clear(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.clear() - remove all elements but keep capacity.

    Sets len to 0, keeps capacity and data pointer.
    Destroys all elements using RAII cleanup.

    Args:
        codegen: LLVM codegen instance.
        list_ptr: Pointer to the List<T> struct.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        Updated List<T> struct value with len=0.
    """
    # Extract element type
    element_type = extract_element_type(list_type, codegen)

    # Use the provided pointer directly
    list_alloca = list_ptr

    # Get len and data pointers
    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = codegen.builder.gep(
        list_alloca,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Destroy all elements (loop from 0 to len-1)
    _emit_destroy_elements_loop(codegen, data_ptr, current_len, element_type)

    # Set len to 0
    zero = ir.Constant(codegen.types.i32, 0)
    codegen.builder.store(zero, len_ptr)

    # Return updated list
    return codegen.builder.load(list_alloca, name="cleared_list")


def _emit_destroy_elements_loop(codegen: Any, data_ptr: ir.Value, count: ir.Value, element_type: Any) -> None:
    """Helper to destroy elements in a loop using RAII cleanup.

    Args:
        codegen: LLVM codegen instance.
        data_ptr: Pointer to array data.
        count: Number of elements to destroy.
        element_type: The semantic element type.
    """
    from backend import gep_utils

    # Create loop blocks
    loop_cond = codegen.func.append_basic_block("destroy_loop_cond")
    loop_body = codegen.func.append_basic_block("destroy_loop_body")
    loop_end = codegen.func.append_basic_block("destroy_loop_end")

    # Initialize loop counter
    zero = ir.Constant(codegen.types.i32, 0)
    counter_alloca = codegen.builder.alloca(codegen.types.i32, name="counter")
    codegen.builder.store(zero, counter_alloca)
    codegen.builder.branch(loop_cond)

    # Loop condition: counter < count
    codegen.builder.position_at_end(loop_cond)
    counter = codegen.builder.load(counter_alloca, name="counter")
    should_continue = codegen.builder.icmp_unsigned("<", counter, count)
    codegen.builder.cbranch(should_continue, loop_body, loop_end)

    # Loop body: destroy element at data[counter]
    codegen.builder.position_at_end(loop_body)
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, counter, "element_ptr")
    element_value = codegen.builder.load(element_ptr, name="element_to_destroy")

    # Use recursive destructor
    from backend.destructors import emit_value_destructor
    emit_value_destructor(codegen, codegen.builder, element_value, element_type)

    # Increment counter
    one = ir.Constant(codegen.types.i32, 1)
    new_counter = codegen.builder.add(counter, one, name="new_counter")
    codegen.builder.store(new_counter, counter_alloca)
    codegen.builder.branch(loop_cond)

    # Continue after loop
    codegen.builder.position_at_end(loop_end)


def emit_list_insert(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.insert(index, element) - insert element at position.

    Returns Result<~>:
    - Result.Ok(~) if insertion successful
    - Result.Err() if index out of bounds (index > len)

    Algorithm:
    1. Bounds check: index must be in range [0, len] (inclusive of len for append)
    2. If len >= capacity, grow capacity (2x strategy)
    3. Shift elements from [index, len) one position right using memmove
    4. Store new element at index
    5. Increment len

    Args:
        codegen: LLVM codegen instance.
        expr: The method call expression with args[0]=index, args[1]=element.
        list_ptr: Pointer to the List<T> struct.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        A Result<~> enum value.
    """
    from backend.expressions import memory
    from backend import gep_utils

    # Extract element type
    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    # Use the provided pointer directly
    list_alloca = list_ptr

    # Get pointers to fields
    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = codegen.builder.gep(
        list_alloca,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(capacity_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Evaluate index argument
    index_value = codegen.expressions.emit_expr(expr.args[0])

    # Bounds check: 0 <= index <= len (note: len is valid for append-like insert)
    zero = ir.Constant(codegen.types.i32, 0)
    index_not_negative = codegen.builder.icmp_signed(">=", index_value, zero)
    index_valid = codegen.builder.icmp_unsigned("<=", index_value, current_len)
    bounds_ok = codegen.builder.and_(index_not_negative, index_valid, name="bounds_ok")

    # Create blocks
    in_bounds_block = codegen.func.append_basic_block("insert_in_bounds")
    out_of_bounds_block = codegen.func.append_basic_block("insert_out_of_bounds")
    end_block = codegen.func.append_basic_block("insert_end")

    # Branch based on bounds check
    codegen.builder.cbranch(bounds_ok, in_bounds_block, out_of_bounds_block)

    # Out of bounds: return Result.Err()
    codegen.builder.position_at_end(out_of_bounds_block)
    from semantics.typesys import BuiltinType
    from backend.generics.results import ensure_result_type_in_table
    result_type = ensure_result_type_in_table(codegen.enum_table, BuiltinType.BLANK)
    result_llvm_type = codegen.types.ll_type(result_type)
    # Result.Err() - tag = 1, no data
    err_enum = ir.Constant(result_llvm_type, ir.Undefined)
    err_enum = codegen.builder.insert_value(err_enum, ir.Constant(codegen.types.i32, 1), 0, name="Result_Err_tag")
    err_block = codegen.builder.block
    codegen.builder.branch(end_block)

    # In bounds: perform insertion
    codegen.builder.position_at_end(in_bounds_block)

    # Check if growth is needed
    need_growth = codegen.builder.icmp_unsigned(">=", current_len, current_cap)

    # Save block for phi node
    before_growth = codegen.builder.block

    with codegen.builder.if_then(need_growth):
        # Calculate new capacity: cap == 0 ? 1 : cap * 2
        one = ir.Constant(codegen.types.i32, 1)
        two = ir.Constant(codegen.types.i32, 2)

        cap_is_zero = codegen.builder.icmp_unsigned("==", current_cap, zero)
        double_cap = codegen.builder.mul(current_cap, two)
        new_cap = codegen.builder.select(cap_is_zero, one, double_cap, name="new_cap")

        # Calculate new size: new_cap * sizeof(T)
        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        new_total_size = codegen.builder.mul(new_cap, element_size, name="new_total_size")

        # Reallocate
        new_data_ptr = memory.emit_realloc_call(codegen, data_ptr, new_total_size)
        typed_new_data_ptr = codegen.builder.bitcast(
            new_data_ptr,
            ir.PointerType(element_llvm_type),
            name="typed_new_data_ptr"
        )

        # Update capacity and data pointer
        codegen.builder.store(new_cap, capacity_ptr)
        codegen.builder.store(typed_new_data_ptr, data_ptr_ptr)
        after_growth = codegen.builder.block

    # Phi node to merge data_ptr
    phi = codegen.builder.phi(data_ptr.type, name="data_ptr_phi")
    phi.add_incoming(data_ptr, before_growth)
    if 'after_growth' in locals():
        phi.add_incoming(typed_new_data_ptr, after_growth)
    data_ptr = phi

    # Now shift elements from [index, len) one position to the right
    # We need to move (len - index) elements
    # Use llvm.memmove for overlapping memory regions
    num_to_move = codegen.builder.sub(current_len, index_value, name="num_to_move")

    # Only do the move if there are elements to shift (index < len)
    has_elements_to_shift = codegen.builder.icmp_unsigned(">", num_to_move, zero)

    with codegen.builder.if_then(has_elements_to_shift):
        # Source: &data[index]
        src_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "src_ptr")

        # Destination: &data[index + 1]
        one = ir.Constant(codegen.types.i32, 1)
        index_plus_one = codegen.builder.add(index_value, one, name="index_plus_one")
        dest_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_plus_one, "dest_ptr")

        # Calculate bytes to move: num_to_move * sizeof(T)
        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        bytes_to_move = codegen.builder.mul(num_to_move, element_size, name="bytes_to_move")

        # Cast pointers to i8* for memmove
        src_i8 = codegen.builder.bitcast(src_ptr, ir.PointerType(codegen.types.i8))
        dest_i8 = codegen.builder.bitcast(dest_ptr, ir.PointerType(codegen.types.i8))

        # Call llvm.memmove intrinsic
        # Signature: void @llvm.memmove.p0i8.p0i8.i32(i8* dest, i8* src, i32 len, i1 is_volatile)
        memmove_fn = codegen.module.declare_intrinsic(
            'llvm.memmove',
            [ir.PointerType(codegen.types.i8), ir.PointerType(codegen.types.i8), codegen.types.i32]
        )
        is_volatile = FALSE_I1
        codegen.builder.call(memmove_fn, [dest_i8, src_i8, bytes_to_move, is_volatile])

    # Evaluate element value to insert
    element_value = codegen.expressions.emit_expr(expr.args[1])

    # Store element at data[index]
    insert_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "insert_ptr")
    codegen.builder.store(element_value, insert_ptr)

    # Increment length
    one = ir.Constant(codegen.types.i32, 1)
    new_len = codegen.builder.add(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    # Return Result.Ok(~)
    # Result.Ok(~) - tag = 0, data contains unit value (i32 0)
    ok_enum = ir.Constant(result_llvm_type, ir.Undefined)
    ok_enum = codegen.builder.insert_value(ok_enum, ir.Constant(codegen.types.i32, 0), 0, name="Result_Ok_tag")
    # Pack unit value into data field
    data_array_type = result_llvm_type.elements[1]
    temp_alloca = codegen.builder.alloca(data_array_type, name="enum_data_temp")
    data_ptr = codegen.builder.bitcast(temp_alloca, ir.PointerType(codegen.types.i8), name="data_ptr")
    # Store unit value (i32 0) at offset 0
    arg_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(codegen.types.i32), name="arg0_ptr_typed")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), arg_ptr)
    # Load packed data and insert into enum
    packed_data = codegen.builder.load(temp_alloca, name="packed_data")
    ok_enum = codegen.builder.insert_value(ok_enum, packed_data, 1, name="Result_Ok_data")
    ok_block = codegen.builder.block
    codegen.builder.branch(end_block)

    # End block with phi for result
    codegen.builder.position_at_end(end_block)
    result_phi = codegen.builder.phi(ok_enum.type, name="insert_result")
    result_phi.add_incoming(err_enum, err_block)
    result_phi.add_incoming(ok_enum, ok_block)

    return result_phi


def emit_list_remove(codegen: Any, expr: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.remove(index) - remove element at position.

    Returns Maybe<T>:
    - Maybe.Some(element) if index is in bounds
    - Maybe.None() if index is out of bounds

    Algorithm:
    1. Bounds check: index must be in range [0, len)
    2. Load element at index (for return value)
    3. Destroy the element at index (RAII cleanup before overwriting)
    4. Shift elements from [index+1, len) one position left using memmove
    5. Decrement len
    6. Return Maybe.Some(element) or Maybe.None()

    Args:
        codegen: LLVM codegen instance.
        expr: The method call expression with args[0]=index.
        list_ptr: Pointer to the List<T> struct.
        list_type: The List<T> struct type from semantic analysis.

    Returns:
        A Maybe<T> enum value.
    """
    from backend.expressions import memory
    from backend import gep_utils
    from backend.generics.maybe import ensure_maybe_type_in_table

    # Extract element type
    element_type = extract_element_type(list_type, codegen)
    list_llvm_type = list_ptr.type.pointee
    element_llvm_type = get_list_element_type(codegen, list_llvm_type)

    # Use the provided pointer directly
    list_alloca = list_ptr

    # Get pointers to fields
    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = codegen.builder.gep(
        list_alloca,
        LIST_DATA_INDICES,
        name="list_data_ptr"
    )

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Evaluate index argument
    index_value = codegen.expressions.emit_expr(expr.args[0])

    # Bounds check: 0 <= index < len
    zero = ir.Constant(codegen.types.i32, 0)
    index_not_negative = codegen.builder.icmp_signed(">=", index_value, zero)
    index_in_bounds = codegen.builder.icmp_unsigned("<", index_value, current_len)
    bounds_ok = codegen.builder.and_(index_not_negative, index_in_bounds, name="bounds_ok")

    # Create blocks
    in_bounds_block = codegen.func.append_basic_block("remove_in_bounds")
    out_of_bounds_block = codegen.func.append_basic_block("remove_out_of_bounds")
    end_block = codegen.func.append_basic_block("remove_end")

    # Branch based on bounds check
    codegen.builder.cbranch(bounds_ok, in_bounds_block, out_of_bounds_block)

    # Out of bounds: return Maybe.None()
    codegen.builder.position_at_end(out_of_bounds_block)
    maybe_type = ensure_maybe_type_in_table(codegen.enum_table, element_type)
    maybe_llvm_type = codegen.types.ll_type(maybe_type)
    # Maybe.None() - tag = 1, no data
    none_enum = ir.Constant(maybe_llvm_type, ir.Undefined)
    none_enum = codegen.builder.insert_value(none_enum, ir.Constant(codegen.types.i32, 1), 0, name="Maybe_None_tag")
    none_block = codegen.builder.block
    codegen.builder.branch(end_block)

    # In bounds: perform removal
    codegen.builder.position_at_end(in_bounds_block)

    # Load element at index (for return value)
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")
    element_value = codegen.builder.load(element_ptr, name="removed_element")

    # Calculate number of elements to shift: (len - index - 1)
    one = ir.Constant(codegen.types.i32, 1)
    num_to_move = codegen.builder.sub(current_len, index_value, name="num_after_index")
    num_to_move = codegen.builder.sub(num_to_move, one, name="num_to_move")

    # Only do the shift if there are elements after this one (index < len - 1)
    has_elements_to_shift = codegen.builder.icmp_unsigned(">", num_to_move, zero)

    with codegen.builder.if_then(has_elements_to_shift):
        # Source: &data[index + 1]
        index_plus_one = codegen.builder.add(index_value, one, name="index_plus_one")
        src_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_plus_one, "src_ptr")

        # Destination: &data[index]
        dest_ptr = element_ptr

        # Calculate bytes to move: num_to_move * sizeof(T)
        element_size = memory.get_element_size_constant(codegen, element_llvm_type)
        bytes_to_move = codegen.builder.mul(num_to_move, element_size, name="bytes_to_move")

        # Cast pointers to i8* for memmove
        src_i8 = codegen.builder.bitcast(src_ptr, ir.PointerType(codegen.types.i8))
        dest_i8 = codegen.builder.bitcast(dest_ptr, ir.PointerType(codegen.types.i8))

        # Call llvm.memmove intrinsic to shift left
        memmove_fn = codegen.module.declare_intrinsic(
            'llvm.memmove',
            [ir.PointerType(codegen.types.i8), ir.PointerType(codegen.types.i8), codegen.types.i32]
        )
        is_volatile = FALSE_I1
        codegen.builder.call(memmove_fn, [dest_i8, src_i8, bytes_to_move, is_volatile])

    # Decrement length
    new_len = codegen.builder.sub(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    # Return Maybe.Some(element)
    # Maybe.Some(T) - tag = 0, data contains element value
    some_enum = ir.Constant(maybe_llvm_type, ir.Undefined)
    some_enum = codegen.builder.insert_value(some_enum, ir.Constant(codegen.types.i32, 0), 0, name="Maybe_Some_tag")

    # Pack element value into data field
    data_array_type = maybe_llvm_type.elements[1]
    temp_alloca = codegen.builder.alloca(data_array_type, name="enum_data_temp")
    data_ptr_enum = codegen.builder.bitcast(temp_alloca, ir.PointerType(codegen.types.i8), name="data_ptr")

    # Store element value at offset 0
    arg_ptr = codegen.builder.bitcast(data_ptr_enum, ir.PointerType(element_llvm_type), name="arg0_ptr_typed")
    codegen.builder.store(element_value, arg_ptr)

    # Load packed data and insert into enum
    packed_data = codegen.builder.load(temp_alloca, name="packed_data")
    some_enum = codegen.builder.insert_value(some_enum, packed_data, 1, name="Maybe_Some_data")
    some_block = codegen.builder.block
    codegen.builder.branch(end_block)

    # End block with phi for result
    codegen.builder.position_at_end(end_block)
    result_phi = codegen.builder.phi(some_enum.type, name="remove_result")
    result_phi.add_incoming(none_enum, none_block)
    result_phi.add_incoming(some_enum, some_block)

    return result_phi
