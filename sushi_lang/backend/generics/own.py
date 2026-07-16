"""
Built-in extension methods for Own<T> generic struct.

INLINE EMISSION ONLY. Own<T> methods work on-demand for all types.

Implemented methods:
- alloc(value: T) -> Own<T>: Allocate heap memory and store value. Takes ownership of
  an owning argument (the source variable is moved, so RAII will not double-free it).
- get() -> T: Borrow the owned value. Returns the payload by value; the binding is a
  NON-owning view of the container's payload and is never registered for RAII cleanup,
  so a nested Own<Own<T>> is not double-freed (issue #106).
- destroy() -> ~: Free the allocated memory (RAII), recursing into the payload.

The Own<T> type is a generic struct for unique ownership of heap data:
    struct Own<T>:
        T* value  # Pointer to heap-allocated data

This module provides heap allocation and RAII cleanup for recursive types.

Note: We use "alloc" instead of "new" because "new" is a reserved keyword
in Sushi (used for dynamic array creation).
"""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.semantics.generics.own import get_own_element_type


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
    size_i64 = ir.Constant(codegen.types.i64, size_bytes)
    void_ptr = emit_malloc(codegen, codegen.builder, size_i64)

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
        arg = call.args[0]
        arg_value = codegen.expressions.emit_expr(arg)
        # Own.alloc takes ownership: if the argument is a named owning variable, move it
        # so its RAII cleanup is skipped (the new Own is now the sole owner). Guarded on
        # an owning type so primitives (copied) are untouched (#106).
        from sushi_lang.semantics.ast import Name
        if isinstance(arg, Name):
            arg_ty = codegen.memory.find_semantic_type(arg.id)
            if arg_ty is not None and _arg_type_is_owning(codegen, arg_ty):
                codegen.memory.mark_struct_as_moved(arg.id)
        return emit_own_alloc(codegen, element_type, arg_value)
    elif call.method == "get":
        return emit_own_get(codegen, own_value, element_type)
    elif call.method == "destroy":
        # Extract variable name from receiver (if it's a Name node)
        from sushi_lang.semantics.ast import Name
        if isinstance(call.receiver, Name):
            var_name = call.receiver.id
            slot = codegen.memory.find_local_slot(var_name)
            if slot is not None:
                # Deep teardown via the general recursive destructor (same as the RAII
                # path), so manual destroy of a nested Own<Own<T>> frees every level.
                from sushi_lang.backend.destructors import emit_value_destructor
                emit_value_destructor(codegen, codegen.builder, slot, own_type)
                codegen.dynamic_arrays.mark_own_destroyed(var_name)
                return ir.Constant(codegen.types.i32, 0)
        # Temporary / non-Name receiver: shallow single free of the loaded value.
        return emit_own_destroy(codegen, own_value)
    else:
        raise_internal_error("CE0080", method=call.method)


def _arg_type_is_owning(codegen: Any, ty: Type) -> bool:
    """Return True if a value of this type carries heap ownership, so passing it into
    Own.alloc() should move (consume) the source variable.

    Covers Own<T> and List<T> (whose single buffer field is a raw pointer that
    needs_cleanup() does not recognise) plus everything needs_cleanup() catches
    (dynamic arrays, structs with owned fields, enums with owned associated data).
    """
    from sushi_lang.backend.destructors import needs_cleanup
    return (
        codegen.dynamic_arrays.is_own_type(ty)
        or codegen.dynamic_arrays.is_list_type(ty)
        or needs_cleanup(ty)
    )


