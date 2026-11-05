"""The emitters of the built-in `Own@(T)` methods, with operands (docs/design/ir.md 8.6).

An `Own@(T)` is `{T*}`: one heap block that holds the payload. `get` loads the payload, a
shallow copy that SLIR holds in a view local when the payload owns; `destroy` destroys the
payload, frees the block and writes a null pointer, so the drop at the scope exit and the
drop of a rebind do nothing. `.clone()` is the rvalue `Clone` (`copy_out`), never a method.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

import llvmlite.ir as ir

from sushi_lang.backend.destructors import emit_value_destructor
from sushi_lang.backend.gep_utils import gep_struct_field
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.own import get_own_element_type
from sushi_lang.semantics.typesys import StructType, Type


def emit_own_alloc(codegen: Any, element_type: Type, value: ir.Value) -> ir.Value:
    """`Own.alloc(value)`: a new heap block that holds `value`, as an `Own@(T)`."""
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


def _pointer(codegen: Any, address: ir.Value) -> ir.Value:
    """The address of the pointer field of the `Own@(T)` at `address`."""
    return gep_struct_field(codegen, address, 0, builder=codegen.builder)


def _get(codegen: Any, address: ir.Value, _own_type: StructType,
         _args: Sequence[ir.Value]) -> ir.Value:
    return codegen.builder.load(codegen.builder.load(_pointer(codegen, address)),
                                name="own_value")


def _destroy(codegen: Any, address: ir.Value, own_type: StructType,
             _args: Sequence[ir.Value]) -> ir.Value:
    emit_value_destructor(codegen, address, own_type)
    field = _pointer(codegen, address)
    codegen.builder.store(ir.Constant(field.type.pointee, None), field)
    return ir.Constant(codegen.types.i32, 0)


def _alloc(codegen: Any, _address: ir.Value, own_type: StructType,
           args: Sequence[ir.Value]) -> ir.Value:
    return emit_own_alloc(codegen, get_own_element_type(own_type), args[0])


_OwnEmitter = Callable[[Any, ir.Value, StructType, Sequence[ir.Value]], ir.Value]

#: One row for each method of `Own@(T)` that the emitter calls; `clone` is the rvalue.
OWN_EMITTERS: Mapping[str, _OwnEmitter] = {
    "get": _get,
    "destroy": _destroy,
    "alloc": _alloc,
}


def emit_own_method(codegen: Any, name: str, address: ir.Value, own_type: StructType,
                    args: Sequence[ir.Value]) -> ir.Value:
    """The value of the method `name` on the `Own@(T)` at `address`, with the values of its
    arguments."""
    emit = OWN_EMITTERS.get(name)
    if emit is None:
        raise_internal_error("CE0080", method=name)
    return emit(codegen, address, own_type, args)
