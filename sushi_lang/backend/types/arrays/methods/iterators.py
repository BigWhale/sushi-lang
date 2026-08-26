"""Array iterator emission for foreach loops."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.backend import gep_utils
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_fixed_array_iter(codegen: 'LLVMCodegen', call: MethodCall, array_slot: ir.Value,
                           receiver_type: ir.ArrayType, element_semantic_type: 'Type',
                           to_i1: bool) -> ir.Value:
    """Emit LLVM IR for fixed array iter() method.

    The receiver arrives as an address from `as_fixed_array_address`, and the element type
    from the receiver's own semantic type. Both used to be re-derived here from the AST,
    which is why `b.slots.iter()` on a field was CE0072 (#480): the element-type lookup went
    through the name tables, and a field has no name in them.
    """
    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    array_length = receiver_type.count

    zero = ir.Constant(codegen.types.i32, 0)
    data_ptr = gep_utils.gep_fixed_array_element(codegen, array_slot, zero)

    from sushi_lang.semantics.typesys import IteratorType

    iterator_type = IteratorType(element_type=element_semantic_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    iterator_slot = codegen.builder.alloca(iterator_struct_type)

    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    length_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, array_length), length_ptr)

    data_ptr_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    codegen.builder.store(data_ptr, data_ptr_ptr)

    return codegen.builder.load(iterator_slot)


def emit_dynamic_array_iter(codegen: 'LLVMCodegen', call: MethodCall, receiver_value: ir.Value,
                             receiver_type: ir.LiteralStructType, to_i1: bool) -> ir.Value:
    """Emit LLVM IR for dynamic array iter() method."""
    if len(call.args) != 0:
        raise_internal_error("CE0071", got=len(call.args))

    len_ptr = gep_utils.gep_dynamic_array_len(codegen, receiver_value)
    length = codegen.builder.load(len_ptr)

    data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, receiver_value)
    data_ptr = codegen.builder.load(data_ptr_ptr)

    from sushi_lang.semantics.typesys import IteratorType, DynamicArrayType as SushiDynamicArrayType

    from sushi_lang.semantics.typesys import ReferenceType
    if isinstance(call.receiver, Name):
        semantic_type = codegen.memory.find_semantic_type(call.receiver.id)
        # A borrowed array (peek/poke T[]) surfaces as a ReferenceType; unwrap it
        # to the referenced DynamicArrayType. The receiver_value is already the
        # array-struct pointer (emit_receiver_value loads the reference slot).
        if isinstance(semantic_type, ReferenceType):
            semantic_type = semantic_type.referenced_type
        if isinstance(semantic_type, SushiDynamicArrayType):
            element_semantic_type = semantic_type.base_type
        else:
            raise_internal_error("CE0042", type=type(semantic_type).__name__)
    else:
        raise_internal_error("CE0072", operation="iter() on a complex dynamic array expression")

    iterator_type = IteratorType(element_type=element_semantic_type)
    iterator_struct_type = codegen.types.get_iterator_struct_type(iterator_type)

    iterator_slot = codegen.builder.alloca(iterator_struct_type)

    index_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 0, "index_ptr")
    codegen.builder.store(ir.Constant(codegen.types.i32, 0), index_ptr)

    length_ptr = gep_utils.gep_struct_field(codegen, iterator_slot, 1, "length_ptr")
    codegen.builder.store(length, length_ptr)

    data_ptr_ptr_out = gep_utils.gep_struct_field(codegen, iterator_slot, 2, "data_ptr_ptr")
    codegen.builder.store(data_ptr, data_ptr_ptr_out)

    return codegen.builder.load(iterator_slot)


