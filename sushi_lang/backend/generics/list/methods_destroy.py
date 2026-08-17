"""List<T> destruction methods: destroy(), free()."""

from typing import Any
from sushi_lang.semantics.typesys import StructType
import llvmlite.ir as ir

from .types import get_list_len_ptr, get_list_capacity_ptr, extract_element_type, get_list_data_ptr


def emit_list_destroy(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.destroy() - destroy all elements and free memory."""
    element_type = extract_element_type(list_type, codegen)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    from .methods_modify import _emit_destroy_elements_loop
    _emit_destroy_elements_loop(codegen, data_ptr, current_len, element_type)

    free_func = codegen.get_free_func()
    data_void_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(codegen.types.i8))
    codegen.builder.call(free_func, [data_void_ptr])

    zero = ir.Constant(codegen.types.i32, 0)
    null_ptr = ir.Constant(data_ptr.type, None)

    codegen.builder.store(zero, len_ptr)
    codegen.builder.store(zero, capacity_ptr)
    codegen.builder.store(null_ptr, data_ptr_ptr)

    return codegen.builder.load(list_alloca, name="destroyed_list")


def emit_list_free(codegen: Any, list_ptr: ir.Value, list_type: StructType) -> ir.Value:
    """Emit LLVM IR for list.free() - destroy elements and reset to empty."""
    element_type = extract_element_type(list_type, codegen)

    list_alloca = list_ptr

    len_ptr = get_list_len_ptr(codegen.builder, list_alloca)
    capacity_ptr = get_list_capacity_ptr(codegen.builder, list_alloca)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_alloca)

    current_len = codegen.builder.load(len_ptr, name="current_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="data_ptr")

    from .methods_modify import _emit_destroy_elements_loop
    _emit_destroy_elements_loop(codegen, data_ptr, current_len, element_type)

    free_func = codegen.get_free_func()
    data_void_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(codegen.types.i8))
    codegen.builder.call(free_func, [data_void_ptr])

    zero = ir.Constant(codegen.types.i32, 0)
    null_ptr = ir.Constant(data_ptr.type, None)

    codegen.builder.store(zero, len_ptr)
    codegen.builder.store(zero, capacity_ptr)
    codegen.builder.store(null_ptr, data_ptr_ptr)

    return codegen.builder.load(list_alloca, name="freed_list")
