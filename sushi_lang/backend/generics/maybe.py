"""Built-in extension methods for Maybe<T> generic enum type."""

from typing import Any, Optional, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.semantics.ast import DotCall, MethodCall
from sushi_lang.semantics.typesys import EnumType, Type
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table


def emit_builtin_maybe_method(
    codegen: Any,
    call: MethodCall | DotCall,
    maybe_value: ir.Value,
    maybe_type: EnumType,
    to_i1: bool
) -> ir.Value:
    """Emit LLVM code for Maybe<T> built-in methods."""
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_tag_check, emit_enum_realise

    if call.method == "is_some":
        return codegen.utils.bool_answer(
            emit_enum_tag_check(codegen, maybe_value, 0, "is_some"), to_i1)
    elif call.method == "is_none":
        return codegen.utils.bool_answer(
            emit_enum_tag_check(codegen, maybe_value, 1, "is_none"), to_i1)
    elif call.method == "realise":
        return emit_enum_realise(codegen, call, maybe_value, maybe_type, "Some")
    elif call.method == "expect":
        return _emit_maybe_expect(codegen, call, maybe_value, maybe_type)
    else:
        raise_internal_error("CE0094", method=call.method)


def emit_maybe_signature_method(
    codegen: Any,
    call: MethodCall | DotCall,
    maybe_value: ir.Value,
    maybe_type: EnumType,
    args: list[ir.Value],
) -> ir.Value:
    """Emit a `Maybe@(T)` method that has a signature (design 8.3).

    The caller settled the receiver and the arguments by their stamped modes. The return
    type is the stamp of the typecheck pass, because a method-level type parameter makes
    it depend on an argument.
    """
    if call.method == "or_err":
        return _emit_maybe_or_err(codegen, call, maybe_value, maybe_type, args[0])
    raise_internal_error("CE0094", method=call.method)


def _emit_maybe_or_err(
    codegen: Any,
    call: MethodCall | DotCall,
    maybe_value: ir.Value,
    maybe_type: EnumType,
    error_value: ir.Value,
) -> ir.Value:
    """Emit `m.or_err(nom e)`: `Some(v)` gives `Ok(v)`, and `None` gives `Err(e)`.

    The method owns the receiver and the error value. The payload moves into the `Ok`
    and the error value moves into the `Err`. On the `Some` path nothing holds the error
    value, so it is destroyed there.
    """
    from sushi_lang.backend.destructors import emit_value_destructor, needs_cleanup
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_tag_check
    from sushi_lang.backend.generics.result_builder import (
        build_err_from_return_type, build_ok_variant, stamped_result_return)
    from sushi_lang.backend.memory.allocas import entry_alloca
    from sushi_lang.semantics.generics.results import result_ok_err
    from sushi_lang.semantics.typesys import BuiltinType

    result_type = stamped_result_return(call)
    some_index = maybe_type.get_variant_index("Some")
    if some_index is None:
        raise_internal_error("CE0092", enum=maybe_type.name)
    ok_type, err_type = result_ok_err(result_type)
    builder = codegen.builder

    error_ll = codegen.types.ll_type(err_type)
    if isinstance(error_value.type, ir.PointerType) and error_value.type.pointee == error_ll:
        error_value = builder.load(error_value, name="or_err_error_arg")
    error_slot = entry_alloca(builder, error_ll, name="or_err_error")
    builder.store(error_value, error_slot)
    result_slot = entry_alloca(builder, codegen.types.ll_type(result_type),
                               name="or_err_result")
    is_some = emit_enum_tag_check(codegen, maybe_value, cast(int, some_index),
                                  "or_err_is_some")

    with builder.if_else(is_some) as (then_block, else_block):
        with then_block:
            payload = None
            if ok_type != BuiltinType.BLANK:
                _is_some, payload = codegen.functions.extract_value_from_result_enum(
                    maybe_value, codegen.types.ll_type(ok_type), ok_type)
            builder.store(build_ok_variant(codegen, result_type, payload), result_slot)
            if needs_cleanup(codegen, err_type):
                emit_value_destructor(codegen, error_slot, err_type)
        with else_block:
            error = builder.load(error_slot, name="or_err_error_value")
            builder.store(build_err_from_return_type(codegen, result_type, error),
                          result_slot)

    return builder.load(result_slot, name="or_err_value")


def _emit_maybe_expect(
    codegen: Any,
    call: MethodCall | DotCall,
    maybe_value: ir.Value,
    maybe_type: EnumType
) -> ir.Value:
    """Emit `maybe.expect(message)` -- Some payload, or "ERROR: msg" to stderr and exit(1)."""
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_expect
    return emit_enum_expect(codegen, call, maybe_value, maybe_type,
                            success_variant_name="Some", label="maybe",
                            missing_variant_code="CE0092", assoc_count_code="CE0093")


def ensure_maybe_type_exists(codegen: 'LLVMCodegen', value_type: Type) -> Optional[EnumType]:
    """Ensure that Maybe<T> exists in the enum table, creating it if necessary."""
    return ensure_maybe_type_in_table(codegen.enum_table, value_type, struct_table=codegen.struct_table.by_name)


def get_maybe_enum_type(codegen: 'LLVMCodegen', value_type: Type) -> ir.Type:
    """Get the LLVM type for Maybe<T> enum."""
    maybe_enum = ensure_maybe_type_exists(codegen, value_type)
    if maybe_enum is None:
        raise_internal_error("CE0047", type=str(value_type))

    return codegen.types.ll_type(maybe_enum)


def emit_maybe_some(codegen: 'LLVMCodegen', value_type: Type, value: ir.Value) -> ir.Value:
    """Emit Maybe.Some(value) constructor."""

    maybe_enum = ensure_maybe_type_exists(codegen, value_type)
    if maybe_enum is None:
        raise_internal_error("CE0047", type=str(value_type))

    llvm_enum_type = codegen.types.get_enum_type(maybe_enum)

    some_index = maybe_enum.get_variant_index("Some")

    enum_value = ir.Constant(llvm_enum_type, ir.Undefined)

    tag = ir.Constant(codegen.types.i32, some_index)
    enum_value = codegen.builder.insert_value(enum_value, tag, 0, name="maybe_some_tag")

    data_array_type = llvm_enum_type.elements[1]  # [N x i8] array
    # ENTRY block: a Maybe built inside a loop must not grow the frame (BUGS.md B1).
    temp_alloca = codegen.memory.entry_alloca(data_array_type, "enum_data_temp")

    data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

    # Store the value at payload offset 0. Natural alignment: the data member is a
    # [K x i64] array (#300 phase 2), so the temp alloca is 8-aligned and the packed-
    # layout `align=1` workaround (#145) is gone.
    value_llvm_type = value.type
    value_ptr_typed = codegen.builder.bitcast(data_ptr, ir.PointerType(value_llvm_type), name="value_ptr_typed")
    codegen.builder.store(value, value_ptr_typed)

    packed_data = codegen.builder.load(temp_alloca, name="packed_data")
    enum_value = codegen.builder.insert_value(enum_value, packed_data, 1, name="maybe_some_value")

    return enum_value


def emit_maybe_none(codegen: 'LLVMCodegen', value_type: Type) -> ir.Value:
    """Emit Maybe.None() constructor."""
    maybe_enum = ensure_maybe_type_exists(codegen, value_type)
    if maybe_enum is None:
        raise_internal_error("CE0047", type=str(value_type))

    llvm_enum_type = codegen.types.get_enum_type(maybe_enum)

    none_index = maybe_enum.get_variant_index("None")

    enum_value = ir.Constant(llvm_enum_type, ir.Undefined)

    tag = ir.Constant(codegen.types.i32, none_index)
    enum_value = codegen.builder.insert_value(enum_value, tag, 0, name="maybe_none_tag")

    # None variant has no associated data, so we just set an undefined data field
    data_array_type = llvm_enum_type.elements[1]  # [N x i8] array
    undef_data = ir.Constant(data_array_type, ir.Undefined)
    enum_value = codegen.builder.insert_value(enum_value, undef_data, 1, name="maybe_none_value")

    return enum_value
