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
from sushi_lang.backend.utils import require_builder
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.collections.strings import METHOD_SPECS, MethodSpec
from sushi_lang.sushi_stdlib.src.signatures import Param

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def string_method_function_type(spec: MethodSpec) -> ir.FunctionType:
    """The LLVM type of the generated `string_<name>`: the receiver, then the arguments."""
    types = [llvm_value_type(ty) for ty in (spec.returns, BuiltinType.STRING, *spec.arg_types)]
    if any(ty is None for ty in types):
        raise_internal_error("CE0077", method=spec.name)
    return_type, *param_types = types
    return ir.FunctionType(return_type, param_types)


def emit_stdlib_string_call(
    codegen: 'LLVMCodegen',
    method: str,
    receiver_value: ir.Value,
    args: list,
    to_i1: bool
) -> ir.Value:
    """Emit a call to a stdlib string method.

    Every argument is emitted ONCE, here, through the built-in call-argument seam (#475).
    An aggregate argument that arrives by pointer -- a `string[]` local for `join` -- is
    loaded to the value the generated function takes.
    """
    from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg
    from sushi_lang.backend.functions import declare_stdlib_function

    require_builder(codegen)
    spec = METHOD_SPECS.get(method)
    if spec is None:
        raise_internal_error("CE0077", method=method)

    arg_values = [emit_borrowed_arg(codegen, arg) for arg in args]
    call_args = [receiver_value, *(as_param_value(codegen, Param(ty), value)
                                   for ty, value in zip(spec.arg_types, arg_values, strict=True))]

    function_type = string_method_function_type(spec)
    stdlib_func = declare_stdlib_function(codegen.module, f"string_{method}",
                                          function_type.return_type, list(function_type.args))
    result = codegen.builder.call(stdlib_func, call_args, name=f"{method}_result")
    if to_i1 and spec.returns == BuiltinType.BOOL:
        result = codegen.utils.as_i1(result)
    return result
