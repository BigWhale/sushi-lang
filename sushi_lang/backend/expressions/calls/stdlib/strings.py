"""Standard library string method call emission, from the method's row (#828).

`METHOD_SPECS` holds each method's arguments and return in Sushi types. This module
turns the row into the LLVM signature through `llvm_value_type`, the same helper the
registry rows use, so the declaration matches the generator byte for byte.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.expressions.calls.stdlib.signatures import as_param_value, llvm_value_type
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.backend.utils import require_builder
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.collections.strings import METHOD_SPECS, MethodSpec
from sushi_lang.sushi_stdlib.src.signatures import Param

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def string_method_function_type(spec: MethodSpec) -> ir.FunctionType:
    """The LLVM type of the generated `string_<name>`: the receiver, then the arguments."""
    sushi_types = (spec.returns, BuiltinType.STRING, *spec.arg_types)
    types = [llvm_value_type(ty) for ty in sushi_types]
    for sushi_type, llvm_type in zip(sushi_types, types, strict=True):
        if llvm_type is None:
            raise_internal_error("CE0145", type=display_type(sushi_type))
    return_type, *param_types = types
    return ir.FunctionType(return_type, param_types)


def emit_string_append(codegen: 'LLVMCodegen', address: ir.Value, piece: ir.Value) -> None:
    """`s := s.concat(x)`, the concat peephole (docs/design/ir.md 8.9): `string_append` of
    the value at `address` and of `piece`, into `address`. It grows a buffer that the
    string owns in place, and copies one that it does not own."""
    from sushi_lang.backend.functions import declare_stdlib_function

    require_builder(codegen)
    old = codegen.builder.load(address, name="appended_to")
    append = declare_stdlib_function(codegen.module, "string_append", old.type,
                                     [old.type, old.type])
    codegen.builder.store(codegen.builder.call(append, [old, piece], name="appended"),
                          address)


def emit_stdlib_string_call(
    codegen: 'LLVMCodegen',
    method: str,
    receiver_value: ir.Value,
    arg_values: list[ir.Value],
) -> ir.Value:
    """Emit a call to a built-in string method from the values of its operands.

    A value that arrives by pointer (a `peek` receiver, a `string[]` argument for `join`)
    is loaded to the value that the generated function takes. The i64 of an index position
    is saturated to the `i32` that the generated function takes (`index_as_i32`), and a
    count traps above the i32 range (`count_as_i32`). `is_empty` and `clone` are inline
    intrinsics with no row.
    """
    from sushi_lang.backend.functions import declare_stdlib_function

    require_builder(codegen)
    receiver = as_param_value(codegen, Param(BuiltinType.STRING), receiver_value)
    if method == "is_empty":
        from sushi_lang.sushi_stdlib.src.collections.strings.compiler import (
            emit_string_is_empty_intrinsic)
        is_empty = emit_string_is_empty_intrinsic(codegen.module)
        return codegen.builder.call(is_empty, [receiver], name="is_empty_result")
    if method == "clone":
        from sushi_lang.backend.ownership import copy_out
        return copy_out(codegen, receiver, BuiltinType.STRING)

    spec = METHOD_SPECS.get(method)
    if spec is None:
        raise_internal_error("CE0077", method=method)

    values = [codegen.utils.count_as_i32(value) if at in spec.counts
              else codegen.utils.index_as_i32(value) if at in spec.indexes else value
              for at, value in enumerate(arg_values)]
    call_args = [receiver, *(as_param_value(codegen, Param(ty), value)
                             for ty, value in zip(spec.arg_types, values, strict=True))]
    function_type = string_method_function_type(spec)
    stdlib_func = declare_stdlib_function(codegen.module, f"string_{method}",
                                          function_type.return_type, list(function_type.args))
    return codegen.builder.call(stdlib_func, call_args, name=f"{method}_result")
