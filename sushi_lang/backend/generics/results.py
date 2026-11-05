"""Built-in extension methods for Result<T, E> generic enum type."""

from typing import Any
from sushi_lang.semantics.ast import DotCall, MethodCall
from sushi_lang.semantics.typesys import EnumType
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error


def emit_builtin_result_method(
    codegen: Any,
    method: str,
    result_value: ir.Value,
) -> ir.Value:
    """A method of `Result@(T, E)` that reads its receiver, over the value of its receiver
    (docs/design/ir.md 8.6). `expect`, `realise` and `err` are no call: SLIR lowering makes
    a switch on the tag of each one."""
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_tag_check

    if method == "is_ok":
        return codegen.utils.as_i8(emit_enum_tag_check(codegen, result_value, 0, "is_ok"))
    if method == "is_err":
        return codegen.utils.as_i8(emit_enum_tag_check(codegen, result_value, 1, "is_err"))
    raise_internal_error("CE0094", method=method)


def emit_result_signature_method(
    codegen: Any,
    call: MethodCall | DotCall,
    result_value: ir.Value,
    result_type: EnumType,
    args: list[ir.Value],
) -> ir.Value:
    """Emit a `Result@(T, E)` method that has a signature (design 8.3).

    The caller settled the receiver and the arguments by their stamped modes. The return
    type is the stamp of the typecheck pass, because a method-level type parameter makes
    it depend on an argument.
    """
    if call.method == "map_err":
        return _emit_result_map_err(codegen, call, result_value, result_type, args[0])
    raise_internal_error("CE0094", method=call.method)


def _emit_result_map_err(
    codegen: Any,
    call: MethodCall | DotCall,
    result_value: ir.Value,
    result_type: EnumType,
    function_value: ir.Value,
) -> ir.Value:
    """Emit `r.map_err(f)`: `Ok(v)` gives `Ok(v)`, and `Err(e)` gives `Err(f(e))`.

    The method owns the receiver. The `Ok` payload moves into the answer. The error moves
    into `f`, which takes it `nom`, and the answer of `f` moves into the `Err`. Nothing is
    left to destroy on either path.
    """
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_tag_check
    from sushi_lang.backend.generics.result_builder import (
        build_err_from_return_type, build_ok_variant, stamped_result_return)
    from sushi_lang.backend.memory.allocas import entry_alloca
    from sushi_lang.backend.runtime.closures import emit_indirect_call
    from sushi_lang.semantics.generics.results import result_ok_err
    from sushi_lang.semantics.typesys import BuiltinType, FunctionType

    answer_type = stamped_result_return(call)
    function_type = (call.callee_param_types or (None,))[0]
    if not isinstance(function_type, FunctionType):
        raise_internal_error(
            "CE0015", message="'map_err' has no stamped function parameter type")
    ok_type, err_type = result_ok_err(result_type)
    ok_index = result_type.get_variant_index("Ok")
    if ok_index is None:
        raise_internal_error("CE0089", enum=result_type.name)
    builder = codegen.builder

    answer_slot = entry_alloca(builder, codegen.types.ll_type(answer_type),
                               name="map_err_result")
    is_ok = emit_enum_tag_check(codegen, result_value, ok_index, "map_err_is_ok")

    with builder.if_else(is_ok) as (then_block, else_block):
        with then_block:
            payload = None
            if ok_type != BuiltinType.BLANK:
                _is_ok, payload = codegen.functions.extract_value_from_result_enum(
                    result_value, codegen.types.ll_type(ok_type), ok_type)
            builder.store(build_ok_variant(codegen, answer_type, payload), answer_slot)
        with else_block:
            _is_ok, error = codegen.functions.extract_value_from_result_enum(
                result_value, codegen.types.ll_type(err_type), err_type)
            converted = emit_indirect_call(codegen, function_value, function_type,
                                           [error], to_i1=False)
            builder.store(build_err_from_return_type(codegen, answer_type, converted),
                          answer_slot)

    return builder.load(answer_slot, name="map_err_value")

