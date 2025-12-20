"""
List<T> iterator method implementation.

This module contains the iter() method for creating iterators from List<T>.
"""

from typing import Any, TYPE_CHECKING
import llvmlite.ir as ir
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.semantics.typesys import StructType
from sushi_lang.backend import gep_utils
from .types import get_list_len_ptr, get_list_data_ptr, extract_element_type
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_list_iter(
    codegen: Any,
    call: MethodCall,
    list_value: ir.Value,
    list_type: StructType
) -> ir.Value:
    """Emit List<T>.iter() -> Iterator<T>

    Creates an iterator struct for use with foreach loops.

    Iterator structure: {i32 current_index, i32 length, T* data_ptr}
    - current_index: starts at 0
    - length: current number of elements in the list
    - data_ptr: pointer to the list's data array

    Args:
        codegen: LLVM codegen instance.
        call: The method call AST node.
        list_value: The List struct pointer.
        list_type: The List<T> struct type.

    Returns:
        Iterator struct value.

    Raises:
        ValueError: If iter() is called with arguments.
        TypeError: If semantic type resolution fails.
    """
    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    # Extract element type from List<T>
    element_semantic_type = extract_element_type(list_type, codegen)

    # Get list fields
    len_ptr = get_list_len_ptr(codegen.builder, list_value)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_value)

    # Load length and data pointer
    length = codegen.builder.load(len_ptr, name="list_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="list_data")

    # Create iterator struct: {i32 current_index, i32 length, T* data_ptr}
    from sushi_lang.semantics.typesys import IteratorType

    iterator_type = IteratorType(element_type=element_semantic_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    # Allocate and initialize iterator struct
    iterator_slot = codegen.builder.alloca(iterator_struct_type, name="list_iterator")

    # Set current_index = 0
    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    # Set length = list length
    length_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    codegen.builder.store(length, length_ptr)

    # Set data_ptr = list data pointer
    data_ptr_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    codegen.builder.store(data_ptr, data_ptr_ptr_out)

    # Load and return the iterator struct
    return codegen.builder.load(iterator_slot, name="iterator")
