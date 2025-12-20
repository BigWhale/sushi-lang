"""
Built-in extension methods for Own<T> generic struct.

INLINE EMISSION ONLY. Own<T> methods work on-demand for all types.

Implemented methods:
- alloc(value: T) -> Own<T>: Allocate heap memory and store value
- get() -> &T: Borrow the owned value (returns reference)
- destroy() -> ~: Free the allocated memory (RAII)

The Own<T> type is a generic struct for unique ownership of heap data:
    struct Own<T>:
        T* value  # Pointer to heap-allocated data

This module provides heap allocation and RAII cleanup for recursive types.

Note: We use "alloc" instead of "new" because "new" is a reserved keyword
in Sushi (used for dynamic array creation).
"""

from typing import Any
from sushi_lang.semantics.ast import MethodCall, Call
from sushi_lang.semantics.typesys import StructType, Type, PointerType
import llvmlite.ir as ir
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error


def is_builtin_own_method(method_name: str) -> bool:
    """Check if a method name is a builtin Own<T> method.

    Args:
        method_name: The name of the method to check.

    Returns:
        True if this is a recognized Own<T> method, False otherwise.
    """
    return method_name in ("alloc", "get", "destroy")


def validate_own_method_with_validator(
    call: MethodCall,
    own_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Own<T> method calls.

    Routes to specific validation functions based on method name.

    Args:
        call: The method call AST node.
        own_type: The Own<T> struct type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    if call.method == "alloc":
        _validate_own_alloc(call, own_type, reporter, validator)
    elif call.method == "get":
        _validate_own_get(call, own_type, reporter)
    elif call.method == "destroy":
        _validate_own_destroy(call, own_type, reporter)
    else:
        # Unknown method - should not happen if is_builtin_own_method was called first
        raise_internal_error("CE0080", method=call.method)


def _validate_own_alloc(
    call: MethodCall,
    own_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Own<T>.alloc(value) method call.

    Validates that exactly 1 argument is provided.
    Type checking of the argument will be done by the validator.

    Args:
        call: The method call AST node.
        own_type: The Own<T> struct type.
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    # Validate argument count
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2016, call.loc,
               method="alloc", expected=1, got=len(call.args))


def _validate_own_get(
    call: MethodCall,
    own_type: StructType,
    reporter: Any
) -> None:
    """Validate Own<T>.get() method call.

    Validates that no arguments are provided.
    Returns &T (reference to owned value).

    Args:
        call: The method call AST node.
        own_type: The Own<T> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc,
               method="get", expected=0, got=len(call.args))


def _validate_own_destroy(
    call: MethodCall,
    own_type: StructType,
    reporter: Any
) -> None:
    """Validate Own<T>.destroy() method call.

    Validates that no arguments are provided.
    Returns ~ (blank/void).

    Args:
        call: The method call AST node.
        own_type: The Own<T> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc,
               method="destroy", expected=0, got=len(call.args))


def emit_own_alloc(codegen: Any, element_type: Type, value: ir.Value) -> ir.Value:
    """Emit Own<T>.alloc(value) -> Own<T>

    Implementation:
    1. Calculate sizeof(T)
    2. Call malloc(sizeof(T))
    3. Cast void* to T*
    4. Store value at pointer
    5. Create Own<T> struct with pointer
    6. Return Own<T>

    Args:
        codegen: LLVM codegen instance
        element_type: The type T
        value: The value to store (already emitted)

    Returns:
        Own<T> struct value containing pointer
    """
    element_llvm_type = codegen.types.ll_type(element_type)
    size_bytes = codegen.types.get_type_size_bytes(element_type)

    # Call malloc(size)
    malloc_func = codegen.get_malloc_func()
    size_i64 = ir.Constant(codegen.types.i64, size_bytes)
    void_ptr = codegen.builder.call(malloc_func, [size_i64], name="own_alloc")

    # Cast void* (i8*) to T*
    typed_ptr = codegen.builder.bitcast(
        void_ptr,
        ir.PointerType(element_llvm_type),
        name="own_ptr"
    )

    # Store value at pointer
    codegen.builder.store(value, typed_ptr)

    # Create Own<T> struct: {T* value}
    own_struct_type = ir.LiteralStructType([ir.PointerType(element_llvm_type)])
    own_value = ir.Constant(own_struct_type, ir.Undefined)
    own_value = codegen.builder.insert_value(own_value, typed_ptr, 0, name="own_struct")

    return own_value


def emit_own_get(codegen: Any, own_value: ir.Value, element_type: Type) -> ir.Value:
    """Emit Own<T>.get() -> T

    Implementation:
    1. Extract pointer from Own<T> struct
    2. Load value from pointer
    3. Return the value

    Args:
        codegen: LLVM codegen instance
        own_value: The Own<T> struct value
        element_type: The type T

    Returns:
        The owned value of type T
    """
    # Extract pointer from struct field 0
    ptr = codegen.builder.extract_value(own_value, 0, name="own_ptr")

    # Load and return the value
    return codegen.builder.load(ptr, name="own_value")


def emit_own_destroy(codegen: Any, own_value: ir.Value, var_name: str | None = None) -> ir.Value:
    """Emit Own<T>.destroy() -> ~

    Implementation:
    1. Extract pointer from Own<T> struct
    2. Cast T* to void* (i8*)
    3. Call free(void*)
    4. Mark variable as destroyed (to prevent RAII double-free)
    5. Return blank value (~)

    Args:
        codegen: LLVM codegen instance
        own_value: The Own<T> struct value
        var_name: The variable name (if known) to mark as destroyed

    Returns:
        Blank value (i32 constant 0)
    """
    # Extract pointer from struct
    ptr = codegen.builder.extract_value(own_value, 0, name="own_ptr_to_free")

    # Cast T* to void* (i8*)
    void_ptr = codegen.builder.bitcast(ptr, ir.PointerType(codegen.types.i8))

    # Call free(void*)
    free_func = codegen.get_free_func()
    codegen.builder.call(free_func, [void_ptr])

    # Mark variable as destroyed to prevent RAII double-free
    if var_name and hasattr(codegen, 'dynamic_arrays'):
        codegen.dynamic_arrays.mark_own_destroyed(var_name)

    # Return blank value (~)
    return ir.Constant(codegen.types.i32, 0)


def emit_builtin_own_method(
    codegen: Any,
    call: MethodCall,
    own_value: ir.Value,
    own_type: StructType
) -> ir.Value:
    """Emit LLVM code for Own<T> built-in methods.

    Args:
        codegen: The LLVM code generator instance.
        call: The method call AST node.
        own_value: The LLVM value of the Own<T> receiver (for get/destroy).
        own_type: The Own<T> struct type (after monomorphization).

    Returns:
        The LLVM value representing the method call result.

    Raises:
        ValueError: If the method is not recognized or has invalid arguments.
    """
    # Extract element type T from Own<T>
    # own_type.name is like "Own<i32>", "Own<string>", etc.
    element_type = get_own_element_type(own_type)

    if call.method == "alloc":
        # Emit the argument value
        arg_value = codegen.expressions.emit_expr(call.args[0])
        return emit_own_alloc(codegen, element_type, arg_value)
    elif call.method == "get":
        return emit_own_get(codegen, own_value, element_type)
    elif call.method == "destroy":
        # Extract variable name from receiver (if it's a Name node)
        from sushi_lang.semantics.ast import Name
        var_name = None
        if isinstance(call.receiver, Name):
            var_name = call.receiver.id
        return emit_own_destroy(codegen, own_value, var_name)
    else:
        raise_internal_error("CE0080", method=call.method)


def get_own_element_type(own_type: StructType) -> Type:
    """Extract element type T from Own<T> struct type.

    Args:
        own_type: The Own<T> struct type (has field "value" of type T*).

    Returns:
        The element type T.
    """
    # Get the "value" field type which is T* (PointerType)
    value_field_type = own_type.fields[0][1]  # First field, second element is type

    # Extract pointee type from PointerType
    if isinstance(value_field_type, PointerType):
        return value_field_type.pointee_type
    else:
        # Fallback: should not happen if Own<T> is properly registered
        raise_internal_error("CE0081", type=str(value_field_type))
