"""List<T> iterator method implementation."""

from typing import Any, TYPE_CHECKING
import llvmlite.ir as ir
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType
from sushi_lang.backend import gep_utils
from .types import get_list_len_ptr, get_list_data_ptr, extract_element_type
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    pass


def emit_list_iter(
    codegen: Any,
    call: MethodCall,
    list_value: ir.Value,
    list_type: StructType
) -> ir.Value:
    """Emit List<T>.iter() -> Iterator<T>"""
    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    element_semantic_type = extract_element_type(list_type, codegen)

    len_ptr = get_list_len_ptr(codegen.builder, list_value)
    data_ptr_ptr = get_list_data_ptr(codegen.builder, list_value)

    length = codegen.builder.load(len_ptr, name="list_len")
    data_ptr = codegen.builder.load(data_ptr_ptr, name="list_data")

    from sushi_lang.semantics.typesys import IteratorType

    iterator_type = IteratorType(element_type=element_semantic_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    iterator_slot = codegen.builder.alloca(iterator_struct_type, name="list_iterator")

    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    length_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    codegen.builder.store(length, length_ptr)

    data_ptr_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    codegen.builder.store(data_ptr, data_ptr_ptr_out)

    return codegen.builder.load(iterator_slot, name="iterator")
