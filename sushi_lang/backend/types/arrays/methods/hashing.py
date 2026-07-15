"""
LLVM emission for the auto-derived array hash() method.

Hash is computed using FNV-1a by combining element hashes with the array length:
    hash = FNV_OFFSET_BASIS
    for each element in array:
        hash = (hash XOR element.hash()) * FNV_PRIME
    # Mix in array length for collision resistance
    hash = (hash XOR length) * FNV_PRIME

Whether an array *may* be hashed, and the registration of the method itself, are
semantic concerns and live in semantics/generics/hashing.py. This module only
supplies the emitter, which it deposits in the shared factory registry at import
time.
"""

from typing import Any
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, Type, BuiltinType, StructType, EnumType
import llvmlite.ir as ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import ZERO_I32, make_i32_const
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.sushi_stdlib.src.common import register_hash_emitter_factory, get_builtin_method
from sushi_lang.backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine


def _emit_fixed_array_hash(array_type: ArrayType) -> Any:
    """Create a hash() emitter function for fixed array types.

    This generates code that combines all element hashes using FNV-1a,
    then mixes in the array length.

    Args:
        array_type: The fixed array type (ArrayType)

    Returns:
        An emitter function that computes the array hash
    """
    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Emit LLVM IR for fixed_array.hash() method."""
        if len(call.args) != 0:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        builder = codegen.builder
        u64 = ir.IntType(INT64_BIT_WIDTH)

        # Initialize hash with FNV offset basis
        hash_value = emit_fnv1a_init(codegen)

        # receiver_value is the array value (either a pointer or direct value)
        # For fixed arrays, it's typically a pointer to the array
        if isinstance(receiver_value.type, ir.PointerType):
            array_ptr = receiver_value
        else:
            # If it's a value, we need to allocate and store it
            array_ptr = builder.alloca(receiver_type, name="array_temp")
            builder.store(receiver_value, array_ptr)

        # Iterate through all elements
        for i in range(array_type.size):
            # Get pointer to element i using GEP
            zero = ZERO_I32
            index = make_i32_const(i)
            element_ptr = builder.gep(array_ptr, [zero, index], name=f"elem_{i}_ptr")

            # Load element value
            element_value = builder.load(element_ptr, name=f"elem_{i}")

            # Get hash of this element
            element_hash = _emit_element_hash(codegen, element_value, array_type.base_type)

            # Combine using FNV-1a: hash = (hash XOR element_hash) * FNV_PRIME
            hash_value = emit_fnv1a_combine(codegen, hash_value, element_hash)

        # Mix in array length for collision resistance
        length_u64 = ir.Constant(u64, array_type.size)
        hash_value = emit_fnv1a_combine(codegen, hash_value, length_u64)

        return hash_value

    return emitter


def _emit_dynamic_array_hash(array_type: DynamicArrayType) -> Any:
    """Create a hash() emitter function for dynamic array types.

    This generates code that combines all element hashes using FNV-1a,
    then mixes in the array length.

    Args:
        array_type: The dynamic array type (DynamicArrayType)

    Returns:
        An emitter function that computes the array hash
    """
    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Emit LLVM IR for dynamic_array.hash() method."""
        if len(call.args) != 0:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        builder = codegen.builder
        i32 = ir.IntType(INT32_BIT_WIDTH)
        u64 = ir.IntType(INT64_BIT_WIDTH)

        # Initialize hash with FNV offset basis
        hash_value_alloca = builder.alloca(u64, name="hash_value")
        initial_hash = emit_fnv1a_init(codegen)
        builder.store(initial_hash, hash_value_alloca)

        # receiver_value is the dynamic array struct value (either a pointer or direct value)
        # The get_dynamic_array_*_ptr functions expect a pointer to the struct
        if isinstance(receiver_value.type, ir.PointerType):
            array_struct_ptr = receiver_value
        else:
            # If it's a value (e.g., from extract_value), allocate temp space
            array_struct_ptr = builder.alloca(receiver_type, name="array_struct_temp")
            builder.store(receiver_value, array_struct_ptr)

        # Get array length and data pointer
        # Extract length field (first field, index 0)
        len_ptr = codegen.types.get_dynamic_array_len_ptr(builder, array_struct_ptr)
        current_len = builder.load(len_ptr, name="array_len")

        # Extract data pointer (third field, index 2)
        data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(builder, array_struct_ptr)
        data_ptr = builder.load(data_ptr_ptr, name="array_data")

        # Create loop: for i in 0..len
        counter = builder.alloca(i32, name="counter")
        zero_i32 = ZERO_I32
        builder.store(zero_i32, counter)

        loop_header = builder.append_basic_block(name="hash_loop_header")
        loop_body = builder.append_basic_block(name="hash_loop_body")
        loop_exit = builder.append_basic_block(name="hash_loop_exit")

        # Jump to loop header
        builder.branch(loop_header)

        # Loop header: check if counter < length
        builder.position_at_end(loop_header)
        current_counter = builder.load(counter)
        cond = builder.icmp_unsigned('<', current_counter, current_len)
        builder.cbranch(cond, loop_body, loop_exit)

        # Loop body: hash element at current index
        builder.position_at_end(loop_body)

        # Get pointer to element at current_counter
        element_ptr = builder.gep(data_ptr, [current_counter], name="element_ptr")
        element_value = builder.load(element_ptr, name="element")

        # Get hash of this element
        element_hash = _emit_element_hash(codegen, element_value, array_type.base_type)

        # Combine using FNV-1a
        current_hash = builder.load(hash_value_alloca)
        new_hash = emit_fnv1a_combine(codegen, current_hash, element_hash)
        builder.store(new_hash, hash_value_alloca)

        # Increment counter
        one_i32 = make_i32_const(1)
        next_counter = builder.add(current_counter, one_i32)
        builder.store(next_counter, counter)

        # Loop back to header
        builder.branch(loop_header)

        # Loop exit: mix in array length
        builder.position_at_end(loop_exit)
        final_hash = builder.load(hash_value_alloca)

        # Mix in length for collision resistance
        length_u64 = builder.zext(current_len, u64)
        final_hash = emit_fnv1a_combine(codegen, final_hash, length_u64)

        return final_hash

    return emitter


def _emit_element_hash(codegen: Any, element_value: ir.Value, element_type: Type) -> ir.Value:
    """Emit code to get the hash of an array element.

    This recursively calls the appropriate .hash() method based on the element type.

    Args:
        codegen: The LLVM code generator
        element_value: LLVM value of the element
        element_type: Semantic type of the element

    Returns:
        Hash value as u64
    """
    builder = require_builder(codegen)
    builder = codegen.builder

    # For primitive types, call their hash() method inline
    if isinstance(element_type, BuiltinType):
        # Special handling for strings - call string hash directly
        if element_type == BuiltinType.STRING:
            from sushi_lang.backend.types.primitives.hashing import _emit_string_hash_fnv1a
            return _emit_string_hash_fnv1a(codegen, element_value)

        # Ensure hash methods are registered
        import sushi_lang.backend.types.primitives.hashing  # noqa: F401

        hash_method = get_builtin_method(element_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(element_type))

        # Create a fake MethodCall for the emitter
        fake_call = MethodCall(
            receiver=Name(id="element", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        # Call the builtin hash emitter directly
        return hash_method.llvm_emitter(
            codegen, fake_call, element_value, element_value.type, False
        )

    # For structs, call their hash() method
    elif isinstance(element_type, StructType):
        hash_method = get_builtin_method(element_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(element_type))

        fake_call = MethodCall(
            receiver=Name(id="element", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, element_value, element_value.type, False
        )

    # For enums, call their hash() method
    elif isinstance(element_type, EnumType):
        hash_method = get_builtin_method(element_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(element_type))

        fake_call = MethodCall(
            receiver=Name(id="element", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, element_value, element_value.type, False
        )

    else:
        raise_internal_error("CE0052", type=str(element_type))


def emit_fixed_array_hash_direct(codegen: Any, expr: Any, receiver_value: ir.Value,
                                 receiver_type: ir.Type, to_i1: bool) -> ir.Value:
    """Direct emitter for fixed array hash (called from backend/expressions/calls.py).

    This is a wrapper that adapts the signature to match the array method calling convention.

    Args:
        codegen: The LLVM code generator
        expr: The method call expression (MethodCall)
        receiver_value: LLVM value of the array
        receiver_type: LLVM type of the array (ir.ArrayType)
        to_i1: Whether to convert result to i1

    Returns:
        Hash value as u64
    """
    from sushi_lang.semantics.ast import Name

    # Get the semantic type from the variable table.
    # The receiver's semantic type must be resolvable here -- semantic analysis
    # guarantees a resolved Name receiver for .hash(). Falling back to an i32 array
    # would silently produce a wrong hash, so a missing type is a compiler bug.
    if isinstance(expr.receiver, Name):
        array_type = codegen.variable_types.get(expr.receiver.id)
        if array_type is None:
            raise_internal_error("CE0056", name=expr.receiver.id)
    else:
        raise_internal_error("CE0056", name=f"<{type(expr.receiver).__name__}>")

    # Create emitter and call it
    emitter = _emit_fixed_array_hash(array_type)
    return emitter(codegen, expr, receiver_value, receiver_type, to_i1)


def emit_dynamic_array_hash_direct(codegen: Any, expr: Any, receiver_value: ir.Value,
                                   receiver_type: ir.Type, to_i1: bool) -> ir.Value:
    """Direct emitter for dynamic array hash (called from backend/expressions/calls.py).

    This is a wrapper that adapts the signature to match the array method calling convention.

    Args:
        codegen: The LLVM code generator
        expr: The method call expression (MethodCall)
        receiver_value: LLVM value of the array struct
        receiver_type: LLVM type of the array struct (ir.LiteralStructType)
        to_i1: Whether to convert result to i1

    Returns:
        Hash value as u64
    """
    from sushi_lang.semantics.ast import Name

    # Get the semantic type from the variable table.
    # As above: a missing receiver type is a compiler bug, not an i32[] fallback.
    if isinstance(expr.receiver, Name):
        array_type = codegen.variable_types.get(expr.receiver.id)
        if array_type is None:
            raise_internal_error("CE0056", name=expr.receiver.id)
    else:
        raise_internal_error("CE0056", name=f"<{type(expr.receiver).__name__}>")

    # Create emitter and call it
    emitter = _emit_dynamic_array_hash(array_type)
    return emitter(codegen, expr, receiver_value, receiver_type, to_i1)




def _make_array_hash_emitter(array_type: Type) -> Any:
    """Build the hash() emitter for an array type, fixed or dynamic."""
    if isinstance(array_type, ArrayType):
        return _emit_fixed_array_hash(array_type)
    if isinstance(array_type, DynamicArrayType):
        return _emit_dynamic_array_hash(array_type)
    raise_internal_error("CE0041", type=type(array_type).__name__)


# Supply the array hash() emitter to semantics/generics/hashing.py, which owns
# hashability analysis and the registration itself.
register_hash_emitter_factory("array", _make_array_hash_emitter)
