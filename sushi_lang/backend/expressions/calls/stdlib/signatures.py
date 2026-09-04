"""How a signature row crosses to C: the LLVM shape, and the marshalling (#550).

`sushi_stdlib/src/signatures.py` holds what a registry primitive takes and answers, in
SUSHI types. This module is the other half: the one place that turns such a row into an
LLVM signature and emits the call. A layer's emitter is then the table plus a symbol
prefix, and the special cases that used to spell every parameter type by hand are gone.

The declared signature must byte-match what the generator in `sushi_stdlib/src` emitted,
which is why the Result and the container types are built through the SAME helpers the
generator used rather than spelled out here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from llvmlite import ir

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType, UnknownType
from sushi_lang.sushi_stdlib.src.signatures import Param, Signature

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

# A `bool` crosses as i8, not i1: that is the ABI the generators emit, and the caller
# narrows with `as_i1` where a condition needs one.
_SCALARS = {
    BuiltinType.I32: lambda: ir.IntType(32),
    BuiltinType.I64: lambda: ir.IntType(64),
    BuiltinType.BOOL: lambda: ir.IntType(8),
    BuiltinType.U8: lambda: ir.IntType(8),
}


def llvm_value_type(ty) -> Optional[ir.Type]:
    """The LLVM type a Sushi type crosses as, BY VALUE, or None if it cannot."""
    from sushi_lang.sushi_stdlib.src.type_definitions import (
        get_byte_array_type, get_datagram_type, get_dynamic_array_type,
        get_maybe_type, get_string_type,
    )

    scalar = _SCALARS.get(ty)
    if scalar is not None:
        return scalar()
    if ty == BuiltinType.STRING:
        return get_string_type()
    if isinstance(ty, DynamicArrayType):
        if ty.base_type == BuiltinType.U8:
            return get_byte_array_type()
        element = llvm_value_type(ty.base_type)
        return None if element is None else get_dynamic_array_type(element)
    if isinstance(ty, GenericTypeRef) and ty.base_name == "Maybe" and len(ty.type_args) == 1:
        inner = llvm_value_type(ty.type_args[0])
        return None if inner is None else get_maybe_type(inner)
    if isinstance(ty, UnknownType) and ty.name == "Datagram":
        # The one named struct a registry primitive answers. Sized from the same
        # helper the generator used, so the Result layout matches byte for byte.
        return get_datagram_type()
    return None


def llvm_param_type(param: Param) -> Optional[ir.Type]:
    """The LLVM type one PARAMETER crosses as. A C string is a pointer, not a value."""
    if param.as_cstr:
        return ir.PointerType(ir.IntType(8))
    return llvm_value_type(param.ty)


def llvm_ok_type(ty) -> Optional[ir.Type]:
    """The LLVM type of a Result's Ok payload."""
    return llvm_value_type(ty)


def llvm_return_type(sig: Signature) -> ir.Type:
    """The LLVM return type of a row: a Result struct, or the bare value."""
    from sushi_lang.sushi_stdlib.src.type_definitions import (
        get_result_type, get_unit_enum_type,
    )

    if sig.ok is None:
        bare = llvm_value_type(sig.bare)
        if bare is None:
            raise_internal_error("CE0024", type="stdlib signature", method=str(sig.bare))
        return bare
    ok = llvm_ok_type(sig.ok)
    if ok is None:
        raise_internal_error("CE0024", type="stdlib signature", method=str(sig.ok))
    # Every registry error enum is a UNIT enum, so the Err arm's size is the same for
    # all of them and the payload word count comes out of the Ok type alone.
    return get_result_type(ok, get_unit_enum_type())


def emit_registry_call(codegen: 'LLVMCodegen', expr, func_name: str, symbol: str,
                       sig: Signature, to_i1: bool) -> ir.Value:
    """Emit one registry stdlib call, straight from its row.

    A `cstr` argument is marshalled through `emit_cstr_arg`, the one C-string seam, and
    is freed at scope exit like an FFI argument (#292); the callee takes `i8*` and frees
    nothing. Everything else crosses as the value it already is -- a descriptor a bare
    i32, an offset a bare i64, a `u8[]` its descriptor by value, a `string` its fat
    pointer -- and each is a BORROW: no callee here frees what it was handed.
    """
    from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg, emit_cstr_arg
    from sushi_lang.backend.functions import declare_stdlib_function

    if len(expr.args) != sig.arity:
        # A compiler bug, not a user error: the layer's `validate_*_call` already
        # refused a wrong count with CE2009.
        raise_internal_error("CE0023", method=func_name,
                             expected=sig.arity, got=len(expr.args))

    args = []
    for param, written in zip(sig.params, expr.args, strict=True):
        if param.as_cstr:
            args.append(emit_cstr_arg(codegen, written))
        else:
            args.append(_by_value(codegen, param, emit_borrowed_arg(codegen, written)))

    params = [llvm_param_type(param) for param in sig.params]
    stdlib_func = declare_stdlib_function(codegen.module, symbol,
                                          llvm_return_type(sig), params)
    result = codegen.builder.call(stdlib_func, args, name=f"{func_name}_result")
    return codegen.utils.as_i1(result) if to_i1 else result


def _by_value(codegen: 'LLVMCodegen', param: Param, value: ir.Value) -> ir.Value:
    """An aggregate BY VALUE, whatever shape the argument arrived in.

    A named local yields the descriptor already; a `peek u8[]` parameter is a reference
    and arrives as a pointer to one. The registry emitters build their calls by hand and
    so do not pass through `cast_for_param`, which is where `emit_named_call` normalizes
    this.
    """
    expected = llvm_param_type(param)
    if (expected is not None and isinstance(value.type, ir.PointerType)
            and value.type.pointee == expected):
        return codegen.builder.load(value, name="arg_by_value")
    return value
