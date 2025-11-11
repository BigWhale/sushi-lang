"""
Extension methods for array hashing.

Implemented methods:
- hash() -> u64: Auto-derived hash function for arrays with hashable element types

Hash is computed using FNV-1a algorithm by combining element hashes with array length:
    hash = FNV_OFFSET_BASIS
    for each element in array:
        hash = (hash XOR element.hash()) * FNV_PRIME
    # Mix in array length for collision resistance
    hash = (hash XOR length) * FNV_PRIME

Known limitations:
- Nested arrays (arrays of arrays) cannot be hashed
- Arrays with unhashable element types cannot be hashed
"""

from typing import Any
from semantics.ast import MethodCall, Name
from semantics.typesys import ArrayType, DynamicArrayType, Type, BuiltinType, StructType, EnumType
import llvmlite.ir as ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from backend.llvm_constants import ZERO_I32, make_i32_const
from internals import errors as er
from internals.errors import raise_internal_error
from stdlib.src.common import register_builtin_method, BuiltinMethod, get_builtin_method
from backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine


def _validate_array_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on array types.

    Checks:
    - No arguments to hash()
    - Array element type is hashable
    - No nested arrays (arrays of arrays)
    """
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{target_type}.hash", expected=0, got=len(call.args))

    # Check if array has nested arrays or unhashable elements
    if isinstance(target_type, (ArrayType, DynamicArrayType)):
        element_type = target_type.base_type

        # Check for nested arrays
        if isinstance(element_type, (ArrayType, DynamicArrayType)):
            er.emit(reporter, er.ERR.CE2051, call.loc,
                   message=f"cannot hash array of arrays (nested arrays not supported)")
            return


def can_array_be_hashed(array_type: Type, visited: set = None, path: list = None) -> tuple[bool, str]:
    """Check if an array type can have an auto-derived hash method.

    An array can be hashed if:
    - It's not a nested array (array of arrays)
    - Its element type is hashable (primitives, structs, enums with hash methods)

    Args:
        array_type: The array type to check (ArrayType or DynamicArrayType)
        visited: Set of type names already visited (for cycle detection)
        path: List of type names in current path (for error messages)

    Returns:
        Tuple of (can_hash, reason) where reason explains why if False
    """
    if not isinstance(array_type, (ArrayType, DynamicArrayType)):
        return False, f"not an array type: {type(array_type).__name__}"

    element_type = array_type.base_type

    # Initialize tracking for recursive calls
    if visited is None:
        visited = set()
    if path is None:
        path = []

    # Check for nested arrays
    if isinstance(element_type, (ArrayType, DynamicArrayType)):
        return False, f"nested array type (arrays of arrays not supported)"

    # Check if element type is hashable
    # Primitives are always hashable
    if isinstance(element_type, BuiltinType):
        return True, "element type is primitive"

    # Structs need to be checked recursively
    if isinstance(element_type, StructType):
        from backend.types.structs import can_struct_be_hashed
        can_hash, reason = can_struct_be_hashed(element_type, visited.copy(), path.copy())
        if not can_hash:
            return False, f"element struct type cannot be hashed: {reason}"
        return True, "element struct type is hashable"

    # Enums need to be checked recursively
    if isinstance(element_type, EnumType):
        from backend.types.enums import can_enum_be_hashed
        can_hash, reason = can_enum_be_hashed(element_type, visited.copy(), path.copy())
        if not can_hash:
            return False, f"element enum type cannot be hashed: {reason}"
        return True, "element enum type is hashable"

    return False, f"element type {element_type} is not hashable"


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

        if codegen.builder is None:
            raise_internal_error("CE0009")
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

        if codegen.builder is None:
            raise_internal_error("CE0009")
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
    if codegen.builder is None:
        raise_internal_error("CE0009")
    builder = codegen.builder

    # For primitive types, call their hash() method inline
    if isinstance(element_type, BuiltinType):
        # Special handling for strings - call string hash directly
        if element_type == BuiltinType.STRING:
            from backend.types.primitives.hashing import _emit_string_hash_fnv1a
            return _emit_string_hash_fnv1a(codegen, element_value)

        # Ensure hash methods are registered
        import backend.types.primitives.hashing  # noqa: F401

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
    from semantics.typesys import ArrayType, BuiltinType

    # Determine the semantic array type from the codegen's variable_types table
    # receiver_type is ir.ArrayType with count and element type
    from semantics.ast import Name

    llvm_element_type = receiver_type.element
    array_size = receiver_type.count

    # Get the semantic type from the variable table
    # The receiver should be a Name node (variable reference)
    if isinstance(expr.receiver, Name):
        array_type = codegen.variable_types.get(expr.receiver.id)
        if array_type is None:
            # Fallback: create a simple i32 array type (should not happen in practice)
            print(f"[WARNING] Cannot find semantic type for variable '{expr.receiver.id}', falling back to i32 array")
            array_type = ArrayType(BuiltinType.I32, array_size)
    else:
        # Receiver is not a simple variable name - this shouldn't happen for .hash() calls
        print(f"[WARNING] Receiver is not a Name node: {type(expr.receiver).__name__}, falling back to i32 array")
        array_type = ArrayType(BuiltinType.I32, array_size)

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
    from semantics.typesys import DynamicArrayType, BuiltinType
    from semantics.ast import Name

    # Get the semantic type from the variable table
    # The receiver should be a Name node (variable reference)
    if isinstance(expr.receiver, Name):
        array_type = codegen.variable_types.get(expr.receiver.id)
        if array_type is None:
            # Fallback: create a simple i32[] type (should not happen in practice)
            print(f"[WARNING] Cannot find semantic type for variable '{expr.receiver.id}', falling back to i32[]")
            array_type = DynamicArrayType(BuiltinType.I32)
    else:
        # Receiver is not a simple variable name - this shouldn't happen for .hash() calls
        print(f"[WARNING] Receiver is not a Name node: {type(expr.receiver).__name__}, falling back to i32[]")
        array_type = DynamicArrayType(BuiltinType.I32)

    # Create emitter and call it
    emitter = _emit_dynamic_array_hash(array_type)
    return emitter(codegen, expr, receiver_value, receiver_type, to_i1)


def register_array_hash_method(array_type: Type) -> None:
    """Register the auto-derived hash() method for an array type.

    This should be called during semantic analysis (Pass 1.8) for each array type
    that can be hashed (i.e., has hashable element type and is not nested).

    Args:
        array_type: The array type to register hash() for (ArrayType or DynamicArrayType)
    """
    can_hash, reason = can_array_be_hashed(array_type)
    if not can_hash:
        return  # Don't register hash for unsupported arrays

    # Check if hash is already registered
    existing_hash = get_builtin_method(array_type, "hash")
    if existing_hash is not None:
        # Skip duplicate registration
        return

    # Choose emitter based on array type
    if isinstance(array_type, ArrayType):
        emitter = _emit_fixed_array_hash(array_type)
    elif isinstance(array_type, DynamicArrayType):
        emitter = _emit_dynamic_array_hash(array_type)
    else:
        raise_internal_error("CE0041", type=type(array_type).__name__)

    register_builtin_method(
        array_type,
        BuiltinMethod(
            name="hash",
            parameter_types=[],
            return_type=BuiltinType.U64,
            description=f"Auto-derived hash for array {array_type}",
            semantic_validator=_validate_array_hash,
            llvm_emitter=emitter,
        )
    )
