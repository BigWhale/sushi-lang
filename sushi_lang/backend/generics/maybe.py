"""Built-in extension methods for Maybe<T> generic enum type."""

from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table


def emit_builtin_maybe_method(
    codegen: Any,
    call: MethodCall,
    maybe_value: ir.Value,
    maybe_type: EnumType,
    to_i1: bool
) -> ir.Value:
    """Emit LLVM code for Maybe<T> built-in methods."""
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_tag_check, emit_enum_realise

    if call.method == "is_some":
        return emit_enum_tag_check(codegen, maybe_value, 0, "is_some")
    elif call.method == "is_none":
        return emit_enum_tag_check(codegen, maybe_value, 1, "is_none")
    elif call.method == "realise":
        return emit_enum_realise(codegen, call, maybe_value, maybe_type, "Some", "Maybe")
    elif call.method == "expect":
        return _emit_maybe_expect(codegen, call, maybe_value, maybe_type)
    else:
        raise_internal_error("CE0094", method=call.method)


def _emit_maybe_expect(
    codegen: Any,
    call: MethodCall,
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
    temp_alloca = codegen.builder.alloca(data_array_type, name="enum_data_temp")

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
