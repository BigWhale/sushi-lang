"""
Dynamic array core methods.

This module handles LLVM IR emission for dynamic array construction and operations:
- Constructors: new(), from(literal)
- Core operations: len(), capacity(), get(index), push(element), pop()
- Mutators: fill(value), reverse()
- Memory management: free(), destroy()

All methods include appropriate bounds checking and memory safety guarantees.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import DynamicArrayNew, DynamicArrayFrom
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.backend import gep_utils

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def _infer_builtin_type_from_llvm(llvm_type: ir.Type) -> BuiltinType:
    """Infer BuiltinType from LLVM type using dispatch table.

    Centralized mapping from LLVM integer types to Sushi BuiltinTypes.

    Args:
        llvm_type: The LLVM type to infer from.

    Returns:
        The corresponding BuiltinType (defaults to I32 for unknown types).
    """
    # Dispatch table for integer width to BuiltinType
    if isinstance(llvm_type, ir.IntType):
        width_to_builtin = {
            32: BuiltinType.I32,
            8: BuiltinType.I8,
            16: BuiltinType.I16,
            64: BuiltinType.I64,
            1: BuiltinType.BOOL,
        }
        return width_to_builtin.get(llvm_type.width, BuiltinType.I32)

    # Default fallback for non-integer types
    return BuiltinType.I32


def emit_dynamic_array_new(codegen: 'LLVMCodegen', expr: DynamicArrayNew) -> ir.Value:
    """Emit new() constructor for dynamic arrays.

    This is essentially a no-op since the array struct is already
    initialized to empty in the variable declaration.
    Returns a placeholder value indicating successful construction.

    Args:
        codegen: The LLVM codegen instance.
        expr: The DynamicArrayNew AST node.

    Returns:
        LLVM value representing the constructed array (placeholder i32 0).
    """
    # For new() constructor, the array is already initialized by declaration
    # We just need to return a null value to indicate success
    # In a full implementation, this might return the array struct itself
    return ir.Constant(codegen.types.i32, 0)


def emit_dynamic_array_from(codegen: 'LLVMCodegen', expr: DynamicArrayFrom) -> ir.Value:
    """Emit from(array_literal) constructor for dynamic arrays.

    Creates a dynamic array struct on the stack and initializes it with the
    provided elements. Returns a pointer to the stack-allocated struct.

    Args:
        codegen: The LLVM codegen instance.
        expr: The DynamicArrayFrom AST node containing the array literal.

    Returns:
        Pointer to stack-allocated dynamic array struct.
    """
    from ..utils import create_dynamic_array_from_elements

    # Evaluate all element expressions
    elements = [codegen.expressions.emit_expr(elem) for elem in expr.elements.elements]

    if not elements:
        # Empty array case
        raise NotImplementedError("Empty from() constructor not yet supported")

    # Get element type from first element
    element_llvm_type = elements[0].type

    # Infer semantic type from the array literal using centralized function
    element_type = _infer_builtin_type_from_llvm(element_llvm_type)

    # Create the dynamic array struct value
    array_struct = create_dynamic_array_from_elements(codegen, element_type, element_llvm_type, elements)

    # Allocate space on stack for the struct and store it
    array_alloca = codegen.builder.alloca(array_struct.type, name="from_array_temp")
    codegen.builder.store(array_struct, array_alloca)

    # Return pointer to the stack-allocated struct
    return array_alloca


def emit_dynamic_array_len(codegen: 'LLVMCodegen', array_value: ir.Value, to_i1: bool) -> ir.Value:
    """Emit code to get the length of a dynamic array.

    Accesses the first field of the dynamic array struct (len: i32).

    Args:
        codegen: The LLVM codegen instance.
        array_value: The dynamic array struct value.
        to_i1: Whether to convert result to i1.

    Returns:
        The length as i32 (or i1 if to_i1 is True).
    """
    # Get pointer to len field (first field, index 0)
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    len_value = codegen.builder.load(len_ptr, name="array_len")

    return codegen.utils.as_i1(len_value) if to_i1 else len_value


def emit_dynamic_array_capacity(codegen: 'LLVMCodegen', array_value: ir.Value, to_i1: bool) -> ir.Value:
    """Emit code to get the capacity of a dynamic array.

    Accesses the second field of the dynamic array struct (cap: i32).

    Args:
        codegen: The LLVM codegen instance.
        array_value: The dynamic array struct value.
        to_i1: Whether to convert result to i1.

    Returns:
        The capacity as i32 (or i1 if to_i1 is True).
    """
    # Get pointer to cap field (second field, index 1)
    cap_ptr = codegen.types.get_dynamic_array_cap_ptr(codegen.builder, array_value)
    cap_value = codegen.builder.load(cap_ptr, name="array_capacity")

    return codegen.utils.as_i1(cap_value) if to_i1 else cap_value


def emit_dynamic_array_get(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                           index_value: ir.Value, to_i1: bool) -> ir.Value:
    """Emit code for safe array element access with bounds checking.

    Performs runtime bounds checking and emits error RE2020 if index is out of bounds.

    Args:
        codegen: The LLVM codegen instance.
        array_value: The dynamic array struct value.
        array_type: The LLVM struct type.
        index_value: The index to access (i32).
        to_i1: Whether to convert result to i1.

    Returns:
        The element value at the specified index.

    Note:
        Emits runtime error RE2020 for out-of-bounds access.
    """
    from sushi_lang.backend import gep_utils

    # Get current array length for bounds checking
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    current_len = codegen.builder.load(len_ptr, name="array_len")

    # Runtime bounds checking: index >= 0 && index < len
    zero = ir.Constant(codegen.types.i32, 0)

    # Check if index >= 0
    index_not_negative = codegen.builder.icmp_signed(">=", index_value, zero, name="index_not_negative")

    # Check if index < len
    index_in_bounds = codegen.builder.icmp_unsigned("<", index_value, current_len, name="index_in_bounds")

    # Both conditions must be true
    bounds_ok = codegen.builder.and_(index_not_negative, index_in_bounds, name="bounds_ok")

    # Create basic blocks for bounds check
    bounds_ok_block = codegen.func.append_basic_block("bounds_ok")
    bounds_fail_block = codegen.func.append_basic_block("bounds_fail")

    # Branch based on bounds check
    codegen.builder.cbranch(bounds_ok, bounds_ok_block, bounds_fail_block)

    # Bounds failure block: emit runtime error and exit
    codegen.builder.position_at_end(bounds_fail_block)
    codegen.runtime.errors.emit_runtime_error_with_values(
        "RE2020",
        "array index %d out of bounds for array of size %d",
        index_value,
        current_len
    )
    # emit_runtime_error_with_values calls exit(), so this block is terminated
    # Add unreachable to satisfy LLVM
    codegen.builder.unreachable()

    # Bounds OK block - normal array access
    codegen.builder.position_at_end(bounds_ok_block)

    # Get data pointer and access element
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)
    data_ptr = codegen.builder.load(data_ptr_ptr, name="array_data")

    # Use GEP to get element pointer
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "element_ptr")

    # Load element value
    element_type = array_type.elements[2].pointee
    element_value = codegen.builder.load(element_ptr, name="element")

    return codegen.utils.as_i1(element_value) if to_i1 else element_value


def emit_dynamic_array_push(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                            element_value: ir.Value) -> ir.Value:
    """Emit code to append an element to a dynamic array.

    Implements full push logic with exponential growth strategy:
    1. Load current len and cap
    2. Check if len >= cap (growth needed)
    3. If growth needed: realloc to 2x capacity and copy elements
    4. Store element at data[len]
    5. Increment len

    Args:
        codegen: The LLVM codegen instance.
        array_value: The dynamic array struct value.
        array_type: The LLVM struct type.
        element_value: The element to append.

    Returns:
        Void value (represented as i32 constant 0).

    Note:
        Uses exponential growth (2x) for amortized O(1) push operations.
        May emit runtime error RE2021 if realloc fails.
    """
    from sushi_lang.backend.expressions import memory

    # Get pointers to struct fields
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    cap_ptr = codegen.types.get_dynamic_array_cap_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    current_cap = codegen.builder.load(cap_ptr, name="current_cap")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Check if growth is needed (len >= cap)
    need_growth = codegen.builder.icmp_unsigned(">=", current_len, current_cap)

    # Save current basic block and function for phi node
    func = codegen.func
    before_if = codegen.builder.block

    with codegen.builder.if_then(need_growth):
        # Calculate new capacity (2x current, min 1)
        zero = ir.Constant(codegen.types.i32, 0)
        one = ir.Constant(codegen.types.i32, 1)
        two = ir.Constant(codegen.types.i32, 2)

        # new_cap = cap == 0 ? 1 : cap * 2
        cap_is_zero = codegen.builder.icmp_unsigned("==", current_cap, zero)
        double_cap = codegen.builder.mul(current_cap, two)
        new_cap = codegen.builder.select(cap_is_zero, one, double_cap, name="new_cap")

        # Calculate element size and new total size
        element_type = array_type.elements[2].pointee
        element_size = memory.get_element_size_constant(codegen, element_type)
        new_total_size = codegen.builder.mul(new_cap, element_size, name="new_total_size")

        # Reallocate memory
        new_data_ptr = memory.emit_realloc_call(codegen, data_ptr, new_total_size)

        # Cast void* to typed pointer
        typed_new_data_ptr = codegen.builder.bitcast(new_data_ptr, ir.PointerType(element_type), name="typed_new_data_ptr")

        # Update capacity and data pointer
        codegen.builder.store(new_cap, cap_ptr)
        codegen.builder.store(typed_new_data_ptr, data_ptr_ptr)
        after_if = codegen.builder.block

    # Create phi node to merge data_ptr from both branches
    phi = codegen.builder.phi(data_ptr.type, name="data_ptr_phi")
    phi.add_incoming(data_ptr, before_if)
    if 'after_if' in locals():
        phi.add_incoming(typed_new_data_ptr, after_if)
    data_ptr = phi

    # Store element at data[len]
    element_ptr = gep_utils.gep_array_element(codegen, data_ptr, current_len, "element_ptr")
    codegen.builder.store(element_value, element_ptr)

    # Increment length
    new_len = codegen.builder.add(current_len, ir.Constant(codegen.types.i32, 1), name="new_len")
    codegen.builder.store(new_len, len_ptr)

    return ir.Constant(codegen.types.i32, 0)


def emit_dynamic_array_pop(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                           to_i1: bool) -> ir.Value:
    """Emit code to remove and return the last element from a dynamic array.

    Implements full pop logic with bounds checking:
    1. Load current length
    2. Check if len > 0 (array not empty)
    3. Load element at data[len-1]
    4. Decrement len
    5. Return loaded element

    Args:
        codegen: The LLVM codegen instance.
        array_value: The dynamic array struct value.
        array_type: The LLVM struct type.
        to_i1: Whether to convert result to i1.

    Returns:
        The popped element value, or zero if array is empty.
    """
    # Get pointers to struct fields
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Check if array is empty (len == 0)
    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    is_empty = codegen.builder.icmp_unsigned("==", current_len, zero)

    # Create basic blocks for empty and non-empty cases
    element_type = array_type.elements[2].pointee
    empty_block = codegen.builder.function.append_basic_block("array_empty")
    non_empty_block = codegen.builder.function.append_basic_block("array_non_empty")
    merge_block = codegen.builder.function.append_basic_block("pop_merge")

    # Branch based on emptiness
    codegen.builder.cbranch(is_empty, empty_block, non_empty_block)

    # Empty case: return zero value
    codegen.builder.position_at_end(empty_block)
    zero_element = ir.Constant(element_type, 0)
    codegen.builder.branch(merge_block)

    # Non-empty case: pop the element
    codegen.builder.position_at_end(non_empty_block)

    # Calculate index of last element (len - 1)
    last_index = codegen.builder.sub(current_len, one, name="last_index")

    # Load the last element
    last_element_ptr = gep_utils.gep_array_element(codegen, data_ptr, last_index, "last_element_ptr")
    popped_element = codegen.builder.load(last_element_ptr, name="popped_element")

    # Decrement length
    new_len = codegen.builder.sub(current_len, one, name="new_len")
    codegen.builder.store(new_len, len_ptr)

    codegen.builder.branch(merge_block)

    # Merge block: phi node to select result
    codegen.builder.position_at_end(merge_block)
    result_phi = codegen.builder.phi(element_type, name="pop_result")
    result_phi.add_incoming(zero_element, empty_block)
    result_phi.add_incoming(popped_element, non_empty_block)

    return codegen.utils.as_i1(result_phi) if to_i1 else result_phi


def emit_dynamic_array_free(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                           element_semantic_type: 'Type') -> ir.Value:
    """Emit code to free all elements of a dynamic array and reset to empty state.

    Similar to HashMap.free(), this clears the array but keeps it usable:
    1. Recursively destroy all elements using emit_value_destructor
    2. Free the old data buffer
    3. Allocate new empty buffer with initial capacity (8)
    4. Reset len=0, cap=8

    Args:
        codegen: The LLVM codegen instance.
        array_value: The dynamic array struct value.
        array_type: The LLVM struct type.
        element_semantic_type: The semantic type of array elements (for destructor).

    Returns:
        Void value (represented as i32 constant 0).
    """
    from sushi_lang.backend.expressions import memory

    # Constants
    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    initial_capacity = ir.Constant(codegen.types.i32, 8)

    # Get pointers to struct fields
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    cap_ptr = codegen.types.get_dynamic_array_cap_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    old_data_ptr = codegen.builder.load(data_ptr_ptr, name="old_data_ptr")

    # Get element type
    element_type = array_type.elements[2].pointee

    # Check if data pointer is not null
    null_ptr = ir.Constant(ir.PointerType(element_type), None)
    is_not_null = codegen.builder.icmp_unsigned("!=", old_data_ptr, null_ptr)

    with codegen.builder.if_then(is_not_null):
        # Check if elements need cleanup (similar to HashMap.free())
        from sushi_lang.backend.destructors import needs_cleanup, emit_value_destructor
        if needs_cleanup(element_semantic_type):
            # Iterate through all elements and destroy each one
            loop_i = codegen.builder.alloca(codegen.types.i32, name="free_loop_i")
            codegen.builder.store(zero, loop_i)

            loop_cond_bb = codegen.builder.append_basic_block(name="free_loop_cond")
            loop_body_bb = codegen.builder.append_basic_block(name="free_loop_body")
            loop_end_bb = codegen.builder.append_basic_block(name="free_loop_end")

            codegen.builder.branch(loop_cond_bb)

            # Loop condition: i < current_len
            codegen.builder.position_at_end(loop_cond_bb)
            i_val = codegen.builder.load(loop_i, name="i_val")
            loop_cond = codegen.builder.icmp_unsigned("<", i_val, current_len, name="loop_cond")
            codegen.builder.cbranch(loop_cond, loop_body_bb, loop_end_bb)

            # Loop body: destroy element[i]
            codegen.builder.position_at_end(loop_body_bb)
            i_val = codegen.builder.load(loop_i, name="i_val")
            element_ptr = codegen.builder.gep(old_data_ptr, [i_val], name="element_ptr")

            # Recursively destroy this element
            emit_value_destructor(codegen, codegen.builder, element_ptr, element_semantic_type)

            # Increment loop counter
            i_next = codegen.builder.add(i_val, one, name="i_next")
            codegen.builder.store(i_next, loop_i)
            codegen.builder.branch(loop_cond_bb)

            # After loop, continue to free the buffer
            codegen.builder.position_at_end(loop_end_bb)

        # Free the old data buffer
        void_ptr = codegen.builder.bitcast(old_data_ptr, ir.PointerType(codegen.types.i8), name="void_ptr")
        memory.emit_free_call(codegen, void_ptr)

    # Allocate new buffer with initial capacity
    element_size = memory.get_element_size_constant(codegen, element_type)
    new_total_size = codegen.builder.mul(initial_capacity, element_size, name="new_total_size")
    new_data_ptr = memory.emit_malloc_call(codegen, new_total_size)

    # Cast to typed pointer
    typed_new_data_ptr = codegen.builder.bitcast(new_data_ptr, ir.PointerType(element_type), name="typed_new_data_ptr")

    # Update array fields: len=0, cap=8, data=new_ptr
    codegen.builder.store(zero, len_ptr)
    codegen.builder.store(initial_capacity, cap_ptr)
    codegen.builder.store(typed_new_data_ptr, data_ptr_ptr)

    return ir.Constant(codegen.types.i32, 0)


def emit_dynamic_array_destroy(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                              array_semantic_type: 'Type') -> ir.Value:
    """Emit code to explicitly destroy a dynamic array (makes it unusable).

    Full destruction that makes the array unusable:
    1. Recursively destroy all elements using emit_value_destructor
    2. Free the data buffer
    3. Reset len=0, cap=0, data=null
    4. Array is unusable after this

    Args:
        codegen: The LLVM codegen instance.
        array_value: The dynamic array struct value.
        array_type: The LLVM struct type.
        array_semantic_type: The semantic DynamicArrayType (for recursive destructor).

    Returns:
        Void value (represented as i32 constant 0).
    """
    # Get pointers to struct fields
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    cap_ptr = codegen.types.get_dynamic_array_cap_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    # Load data pointer
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Check if data pointer is not null (avoid double-free)
    element_type = array_type.elements[2].pointee
    null_ptr = ir.Constant(ir.PointerType(element_type), None)
    is_not_null = codegen.builder.icmp_unsigned("!=", data_ptr, null_ptr)

    # Free memory only if pointer is not null
    with codegen.builder.if_then(is_not_null):
        # Use the general recursive destructor which handles element cleanup and buffer freeing
        # This automatically handles nested structures, enums, arrays, etc.
        from sushi_lang.backend.destructors import emit_value_destructor
        emit_value_destructor(codegen, codegen.builder, array_value, array_semantic_type)

    # Reset all struct fields to zero/null
    zero = ir.Constant(codegen.types.i32, 0)
    codegen.builder.store(zero, len_ptr)
    codegen.builder.store(zero, cap_ptr)
    codegen.builder.store(null_ptr, data_ptr_ptr)

    return ir.Constant(codegen.types.i32, 0)


def emit_dynamic_array_fill(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType,
                            fill_value: ir.Value) -> ir.Value:
    """Emit code to fill all elements of a dynamic array with a value.

    Fills the entire array [0..len) with the specified value in-place.

    Args:
        codegen: The LLVM codegen instance.
        array_value: The dynamic array struct value.
        array_type: The LLVM struct type.
        fill_value: The value to fill the array with.

    Returns:
        Void value (represented as i32 constant 0).
    """
    # Get pointers to struct fields
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Check if array is empty (len == 0)
    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    is_empty = codegen.builder.icmp_unsigned("==", current_len, zero)

    # Only fill if array is not empty
    with codegen.builder.if_then(codegen.builder.not_(is_empty)):
        # Create loop: for i = 0; i < len; i++
        loop_i = codegen.builder.alloca(codegen.types.i32, name="fill_loop_i")
        codegen.builder.store(zero, loop_i)

        loop_cond_bb = codegen.builder.append_basic_block(name="fill_loop_cond")
        loop_body_bb = codegen.builder.append_basic_block(name="fill_loop_body")
        loop_end_bb = codegen.builder.append_basic_block(name="fill_loop_end")

        codegen.builder.branch(loop_cond_bb)

        # Loop condition: i < current_len
        codegen.builder.position_at_end(loop_cond_bb)
        i_val = codegen.builder.load(loop_i, name="i_val")
        loop_cond = codegen.builder.icmp_unsigned("<", i_val, current_len, name="loop_cond")
        codegen.builder.cbranch(loop_cond, loop_body_bb, loop_end_bb)

        # Loop body: data[i] = fill_value
        codegen.builder.position_at_end(loop_body_bb)
        i_val = codegen.builder.load(loop_i, name="i_val")
        element_ptr = gep_utils.gep_array_element(codegen, data_ptr, i_val, "element_ptr")
        codegen.builder.store(fill_value, element_ptr)

        # Increment loop counter
        i_next = codegen.builder.add(i_val, one, name="i_next")
        codegen.builder.store(i_next, loop_i)
        codegen.builder.branch(loop_cond_bb)

        # After loop
        codegen.builder.position_at_end(loop_end_bb)

    return ir.Constant(codegen.types.i32, 0)


def emit_dynamic_array_reverse(codegen: 'LLVMCodegen', array_value: ir.Value, array_type: ir.LiteralStructType) -> ir.Value:
    """Emit code to reverse a dynamic array in-place.

    Uses two-pointer swap algorithm: swap arr[i] with arr[len-1-i] for i in [0..len/2).

    Args:
        codegen: The LLVM codegen instance.
        array_value: The dynamic array struct value.
        array_type: The LLVM struct type.

    Returns:
        Void value (represented as i32 constant 0).
    """
    # Get pointers to struct fields
    len_ptr = codegen.types.get_dynamic_array_len_ptr(codegen.builder, array_value)
    data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(codegen.builder, array_value)

    # Load current values
    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    # Constants
    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    two = ir.Constant(codegen.types.i32, 2)

    # Check if array has fewer than 2 elements
    is_trivial = codegen.builder.icmp_unsigned("<", current_len, two)

    # Only reverse if array has 2+ elements
    with codegen.builder.if_then(codegen.builder.not_(is_trivial)):
        # Calculate half_len = len / 2
        half_len = codegen.builder.udiv(current_len, two, name="half_len")

        # Get element type for temporary storage
        element_type = array_type.elements[2].pointee
        temp_var = codegen.builder.alloca(element_type, name="temp")

        # Create loop: for i = 0; i < half_len; i++
        loop_i = codegen.builder.alloca(codegen.types.i32, name="reverse_loop_i")
        codegen.builder.store(zero, loop_i)

        loop_cond_bb = codegen.builder.append_basic_block(name="reverse_loop_cond")
        loop_body_bb = codegen.builder.append_basic_block(name="reverse_loop_body")
        loop_end_bb = codegen.builder.append_basic_block(name="reverse_loop_end")

        codegen.builder.branch(loop_cond_bb)

        # Loop condition: i < half_len
        codegen.builder.position_at_end(loop_cond_bb)
        i_val = codegen.builder.load(loop_i, name="i_val")
        loop_cond = codegen.builder.icmp_unsigned("<", i_val, half_len, name="loop_cond")
        codegen.builder.cbranch(loop_cond, loop_body_bb, loop_end_bb)

        # Loop body: swap arr[i] with arr[len-1-i]
        codegen.builder.position_at_end(loop_body_bb)
        i_val = codegen.builder.load(loop_i, name="i_val")

        # Calculate j = len - 1 - i
        len_minus_one = codegen.builder.sub(current_len, one, name="len_minus_one")
        j_val = codegen.builder.sub(len_minus_one, i_val, name="j_val")

        # Get pointers to arr[i] and arr[j]
        left_ptr = gep_utils.gep_array_element(codegen, data_ptr, i_val, "left_ptr")
        right_ptr = gep_utils.gep_array_element(codegen, data_ptr, j_val, "right_ptr")

        # Swap: temp = arr[i]; arr[i] = arr[j]; arr[j] = temp
        left_val = codegen.builder.load(left_ptr, name="left_val")
        codegen.builder.store(left_val, temp_var)

        right_val = codegen.builder.load(right_ptr, name="right_val")
        codegen.builder.store(right_val, left_ptr)

        temp_val = codegen.builder.load(temp_var, name="temp_val")
        codegen.builder.store(temp_val, right_ptr)

        # Increment loop counter
        i_next = codegen.builder.add(i_val, one, name="i_next")
        codegen.builder.store(i_next, loop_i)
        codegen.builder.branch(loop_cond_bb)

        # After loop
        codegen.builder.position_at_end(loop_end_bb)

    return ir.Constant(codegen.types.i32, 0)

def emit_fixed_array_fill(codegen: 'LLVMCodegen', array_ptr: ir.Value, array_type: ir.ArrayType,
                          fill_value: ir.Value) -> ir.Value:
    """Emit code to fill all elements of a fixed array with a value.

    Fills the entire array with the specified value in-place.

    Args:
        codegen: The LLVM codegen instance.
        array_ptr: Pointer to the fixed array.
        array_type: The LLVM array type.
        fill_value: The value to fill the array with.

    Returns:
        Void value (represented as i32 constant 0).
    """
    # Constants
    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    array_size = ir.Constant(codegen.types.i32, array_type.count)

    # Create loop: for i = 0; i < array_size; i++
    loop_i = codegen.builder.alloca(codegen.types.i32, name="fill_loop_i")
    codegen.builder.store(zero, loop_i)

    loop_cond_bb = codegen.builder.append_basic_block(name="fill_loop_cond")
    loop_body_bb = codegen.builder.append_basic_block(name="fill_loop_body")
    loop_end_bb = codegen.builder.append_basic_block(name="fill_loop_end")

    codegen.builder.branch(loop_cond_bb)

    # Loop condition: i < array_size
    codegen.builder.position_at_end(loop_cond_bb)
    i_val = codegen.builder.load(loop_i, name="i_val")
    loop_cond = codegen.builder.icmp_unsigned("<", i_val, array_size, name="loop_cond")
    codegen.builder.cbranch(loop_cond, loop_body_bb, loop_end_bb)

    # Loop body: arr[i] = fill_value
    codegen.builder.position_at_end(loop_body_bb)
    i_val = codegen.builder.load(loop_i, name="i_val")
    element_ptr = codegen.builder.gep(array_ptr, [zero, i_val], name="element_ptr")
    codegen.builder.store(fill_value, element_ptr)

    # Increment loop counter
    i_next = codegen.builder.add(i_val, one, name="i_next")
    codegen.builder.store(i_next, loop_i)
    codegen.builder.branch(loop_cond_bb)

    # After loop
    codegen.builder.position_at_end(loop_end_bb)

    return ir.Constant(codegen.types.i32, 0)


def emit_fixed_array_reverse(codegen: 'LLVMCodegen', array_ptr: ir.Value, array_type: ir.ArrayType) -> ir.Value:
    """Emit code to reverse a fixed array in-place.

    Uses two-pointer swap algorithm: swap arr[i] with arr[len-1-i] for i in [0..len/2).

    Args:
        codegen: The LLVM codegen instance.
        array_ptr: Pointer to the fixed array.
        array_type: The LLVM array type.

    Returns:
        Void value (represented as i32 constant 0).
    """
    # Constants
    zero = ir.Constant(codegen.types.i32, 0)
    one = ir.Constant(codegen.types.i32, 1)
    two = ir.Constant(codegen.types.i32, 2)
    array_size = array_type.count

    # If array has fewer than 2 elements, nothing to reverse
    if array_size < 2:
        return ir.Constant(codegen.types.i32, 0)

    # Calculate half_len = array_size / 2
    half_len = ir.Constant(codegen.types.i32, array_size // 2)
    array_size_const = ir.Constant(codegen.types.i32, array_size)

    # Get element type for temporary storage
    element_type = array_type.element
    temp_var = codegen.builder.alloca(element_type, name="temp")

    # Create loop: for i = 0; i < half_len; i++
    loop_i = codegen.builder.alloca(codegen.types.i32, name="reverse_loop_i")
    codegen.builder.store(zero, loop_i)

    loop_cond_bb = codegen.builder.append_basic_block(name="reverse_loop_cond")
    loop_body_bb = codegen.builder.append_basic_block(name="reverse_loop_body")
    loop_end_bb = codegen.builder.append_basic_block(name="reverse_loop_end")

    codegen.builder.branch(loop_cond_bb)

    # Loop condition: i < half_len
    codegen.builder.position_at_end(loop_cond_bb)
    i_val = codegen.builder.load(loop_i, name="i_val")
    loop_cond = codegen.builder.icmp_unsigned("<", i_val, half_len, name="loop_cond")
    codegen.builder.cbranch(loop_cond, loop_body_bb, loop_end_bb)

    # Loop body: swap arr[i] with arr[size-1-i]
    codegen.builder.position_at_end(loop_body_bb)
    i_val = codegen.builder.load(loop_i, name="i_val")

    # Calculate j = size - 1 - i
    size_minus_one = codegen.builder.sub(array_size_const, one, name="size_minus_one")
    j_val = codegen.builder.sub(size_minus_one, i_val, name="j_val")

    # Get pointers to arr[i] and arr[j]
    left_ptr = codegen.builder.gep(array_ptr, [zero, i_val], name="left_ptr")
    right_ptr = codegen.builder.gep(array_ptr, [zero, j_val], name="right_ptr")

    # Swap: temp = arr[i]; arr[i] = arr[j]; arr[j] = temp
    left_val = codegen.builder.load(left_ptr, name="left_val")
    codegen.builder.store(left_val, temp_var)

    right_val = codegen.builder.load(right_ptr, name="right_val")
    codegen.builder.store(right_val, left_ptr)

    temp_val = codegen.builder.load(temp_var, name="temp_val")
    codegen.builder.store(temp_val, right_ptr)

    # Increment loop counter
    i_next = codegen.builder.add(i_val, one, name="i_next")
    codegen.builder.store(i_next, loop_i)
    codegen.builder.branch(loop_cond_bb)

    # After loop
    codegen.builder.position_at_end(loop_end_bb)

    return ir.Constant(codegen.types.i32, 0)
