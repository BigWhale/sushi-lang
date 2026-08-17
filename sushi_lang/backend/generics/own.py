"""Built-in extension methods for Own<T> generic struct."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.semantics.generics.own import get_own_element_type


def emit_own_alloc(codegen: Any, element_type: Type, value: ir.Value) -> ir.Value:
    """Emit Own<T>.alloc(value) -> Own<T>"""
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
    """Emit Own<T>.get() -> T"""
    # Extract pointer from struct field 0
    ptr = codegen.builder.extract_value(own_value, 0, name="own_ptr")

    # Load and return the value
    return codegen.builder.load(ptr, name="own_value")


def emit_own_destroy(codegen: Any, own_value: ir.Value) -> ir.Value:
    """Emit Own<T>.destroy() -> ~ for a TEMPORARY (non-Name) receiver."""
    # Extract pointer from struct
    ptr = codegen.builder.extract_value(own_value, 0, name="own_ptr_to_free")

    # Cast T* to void* (i8*)
    void_ptr = codegen.builder.bitcast(ptr, ir.PointerType(codegen.types.i8))

    # Call free(void*)
    free_func = codegen.get_free_func()
    codegen.builder.call(free_func, [void_ptr])

    # Return blank value (~)
    return ir.Constant(codegen.types.i32, 0)


def emit_builtin_own_method(
    codegen: Any,
    call: MethodCall,
    own_value: ir.Value,
    own_type: StructType
) -> ir.Value:
    """Emit LLVM code for Own<T> built-in methods."""
    # Extract element type T from Own<T>
    # own_type.name is like "Own<i32>", "Own<string>", etc.
    element_type = get_own_element_type(own_type)

    if call.method == "alloc":
        # Emit the argument value
        arg = call.args[0]
        arg_value = codegen.expressions.emit_expr(arg)
        # `Own.alloc` takes ownership: the new Own becomes the sole owner of the pointee.
        # This position used to move a bare owning Name and do nothing else -- no copy
        # branch at all, so a borrowed binding or a field read was silently aliased.
        from sushi_lang.backend.ownership import ConsumingUse, consume
        arg_value = consume(codegen, arg, arg_value, element_type, ConsumingUse.OWN_ALLOC)
        return emit_own_alloc(codegen, element_type, arg_value)
    elif call.method == "get":
        return emit_own_get(codegen, own_value, element_type)
    elif call.method == "clone":
        # The explicit escape from CE2411 (#242). An `Own@(T).get()` deref BORROWS -- the
        # receiver keeps the pointee and still frees it -- so a position that takes
        # ownership rejects it, and this is what the diagnostic tells the user to write.
        # Routed through the seam's `copy_out`, the ONE deep clone in the backend, so
        # `.clone()` duplicates exactly what `emit_own_destroy` frees: a fresh allocation
        # holding a deep copy of the pointee.
        from sushi_lang.backend.ownership import copy_out
        return copy_out(codegen, own_value, own_type)
    elif call.method == "destroy":
        # Extract variable name from receiver (if it's a Name node)
        from sushi_lang.semantics.ast import Name
        if isinstance(call.receiver, Name):
            var_name = call.receiver.id
            # find_local_slot is the ASSERTIVE form: a miss is CE0055, never None
            # (the semantic passes already accepted the name), so there is no
            # not-found branch here (11b).
            slot = codegen.memory.find_local_slot(var_name)
            # Deep teardown via the general recursive destructor (same as the RAII
            # path), so manual destroy of a nested Own<Own<T>> frees every level.
            from sushi_lang.backend.destructors import emit_value_destructor
            emit_value_destructor(codegen, slot, own_type)
            codegen.dynamic_arrays.mark_own_destroyed(var_name)
            return ir.Constant(codegen.types.i32, 0)
        # Temporary / non-Name receiver: shallow single free of the loaded value.
        return emit_own_destroy(codegen, own_value)
    else:
        raise_internal_error("CE0080", method=call.method)


