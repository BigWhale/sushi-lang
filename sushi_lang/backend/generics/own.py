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

    size_i64 = ir.Constant(codegen.types.i64, size_bytes)
    void_ptr = emit_malloc(codegen, codegen.builder, size_i64)

    typed_ptr = codegen.builder.bitcast(
        void_ptr,
        ir.PointerType(element_llvm_type),
        name="own_ptr"
    )

    codegen.builder.store(value, typed_ptr)

    own_struct_type = ir.LiteralStructType([ir.PointerType(element_llvm_type)])
    own_value = ir.Constant(own_struct_type, ir.Undefined)
    own_value = codegen.builder.insert_value(own_value, typed_ptr, 0, name="own_struct")

    return own_value


def emit_own_get(codegen: Any, own_value: ir.Value, element_type: Type) -> ir.Value:
    """Emit Own<T>.get() -> T"""
    ptr = codegen.builder.extract_value(own_value, 0, name="own_ptr")

    return codegen.builder.load(ptr, name="own_value")


def emit_own_destroy(codegen: Any, own_value: ir.Value) -> ir.Value:
    """Emit Own<T>.destroy() -> ~ for a TEMPORARY (non-Name) receiver."""
    ptr = codegen.builder.extract_value(own_value, 0, name="own_ptr_to_free")

    void_ptr = codegen.builder.bitcast(ptr, ir.PointerType(codegen.types.i8))

    free_func = codegen.get_free_func()
    codegen.builder.call(free_func, [void_ptr])

    return ir.Constant(codegen.types.i32, 0)


def emit_builtin_own_method(
    codegen: Any,
    call: MethodCall,
    own_value: ir.Value,
    own_type: StructType
) -> ir.Value:
    """Emit LLVM code for Own<T> built-in methods."""
    element_type = get_own_element_type(own_type)

    if call.method == "alloc":
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
        # The escape from CE2411 (#242): an `Own@(T).get()` deref BORROWS. Routed through
        # the seam's `copy_out`, so a clone duplicates exactly what `emit_own_destroy`
        # frees.
        from sushi_lang.backend.ownership import copy_out
        return copy_out(codegen, own_value, own_type)
    elif call.method == "destroy":
        from sushi_lang.semantics.ast import Name
        if isinstance(call.receiver, Name):
            var_name = call.receiver.id
            # find_local_slot is the ASSERTIVE form: a miss is CE0055, never None
            # (the semantic passes already accepted the name), so there is no
            # not-found branch here (11b).
            slot = codegen.memory.find_local_slot(var_name)
            from sushi_lang.backend.destructors import emit_value_destructor
            emit_value_destructor(codegen, slot, own_type)
            codegen.dynamic_arrays.mark_own_destroyed(var_name)
            return ir.Constant(codegen.types.i32, 0)
        return emit_own_destroy(codegen, own_value)
    else:
        raise_internal_error("CE0080", method=call.method)


