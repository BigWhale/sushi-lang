"""
Built-in extension methods for Maybe<T> generic enum type.

INLINE EMISSION ONLY. Maybe<T> methods work on-demand for all types.

There is no stdlib IR generation because monomorphizing for all possible user
types is impractical. Unlike Result<T> which only needs to handle a fixed set
of types, Maybe<T> must support any type T that users define (custom structs,
nested generics, etc.). Pre-generating all possible instantiations is not
feasible.

See docs/stdlib/ISSUES.md for why Maybe<T> cannot be moved to stdlib.

Implemented methods:
- is_some() -> bool: Check if value is present (Some variant)
- is_none() -> bool: Check if value is absent (None variant)
- realise(default: T) -> T: Extract Some value or return default if None
- expect(message: string) -> T: Extract Some value or panic with message if None

The Maybe<T> type is a generic enum with two variants:
- Some(T): Contains a value of type T
- None(): Represents absence of value

This module provides ergonomic optional value handling methods that work with
the Maybe<T> type after monomorphization.
"""

from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table


# ==============================================================================
# Inline Emission (on-demand code generation)
# ==============================================================================


def emit_builtin_maybe_method(
    codegen: Any,
    call: MethodCall,
    maybe_value: ir.Value,
    maybe_type: EnumType,
    to_i1: bool
) -> ir.Value:
    """Emit LLVM code for Maybe<T> built-in methods.

    Args:
        codegen: The LLVM code generator instance.
        call: The method call AST node.
        maybe_value: The LLVM value of the Maybe<T> receiver.
        maybe_type: The Maybe<T> enum type (after monomorphization).
        to_i1: Whether to convert result to i1 (for is_some/is_none).

    Returns:
        The LLVM value representing the method call result.

    Raises:
        ValueError: If the method is not recognized or has invalid arguments.
    """
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
    """Emit `maybe.expect(message)` -- Some payload, or "ERROR: msg" to stderr and exit(1).

    Thin adapter over the shared `emit_enum_expect`; the Result spelling is its twin.
    """
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_expect
    return emit_enum_expect(codegen, call, maybe_value, maybe_type,
                            success_variant_name="Some", label="maybe",
                            missing_variant_code="CE0092", assoc_count_code="CE0093")


# ==============================================================================
# Helper Functions for Dynamic Maybe<T> Creation
# ==============================================================================


def ensure_maybe_type_exists(codegen: 'LLVMCodegen', value_type: Type) -> Optional[EnumType]:
    """Ensure that Maybe<T> exists in the enum table, creating it if necessary.

    Convenience wrapper for code generation phase that extracts enum_table from codegen.

    Args:
        codegen: The LLVM codegen instance.
        value_type: The T type parameter for Maybe<T>.

    Returns:
        The EnumType for Maybe<T>, or None if it couldn't be created.
    """
    return ensure_maybe_type_in_table(codegen.enum_table, value_type, struct_table=codegen.struct_table.by_name)


def get_maybe_enum_type(codegen: 'LLVMCodegen', value_type: Type) -> ir.Type:
    """Get the LLVM type for Maybe<T> enum.

    Args:
        codegen: The LLVM codegen instance.
        value_type: The T type parameter for Maybe<T>.

    Returns:
        The LLVM struct type for Maybe<T>.
    """
    maybe_enum = ensure_maybe_type_exists(codegen, value_type)
    if maybe_enum is None:
        raise_internal_error("CE0047", type=str(value_type))

    return codegen.types.ll_type(maybe_enum)


def emit_maybe_some(codegen: 'LLVMCodegen', value_type: Type, value: ir.Value) -> ir.Value:
    """Emit Maybe.Some(value) constructor.

    Args:
        codegen: The LLVM codegen instance.
        value_type: The T type parameter for Maybe<T>.
        value: The LLVM value to wrap in Some.

    Returns:
        The constructed Maybe<T> enum value with Some variant.
    """

    # Ensure Maybe<T> type exists
    maybe_enum = ensure_maybe_type_exists(codegen, value_type)
    if maybe_enum is None:
        raise_internal_error("CE0047", type=str(value_type))

    # Get the LLVM enum type: {i32 tag, [N x i8] data}
    llvm_enum_type = codegen.types.get_enum_type(maybe_enum)

    # Get Some variant index (should be 0)
    some_index = maybe_enum.get_variant_index("Some")

    # Create undefined enum value
    enum_value = ir.Constant(llvm_enum_type, ir.Undefined)

    # Set the tag (discriminant) for Some variant
    tag = ir.Constant(codegen.types.i32, some_index)
    enum_value = codegen.builder.insert_value(enum_value, tag, 0, name="maybe_some_tag")

    # Pack the value into the data field
    data_array_type = llvm_enum_type.elements[1]  # [N x i8] array
    temp_alloca = codegen.builder.alloca(data_array_type, name="enum_data_temp")

    # Cast to i8* for bitcasting
    data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

    # Store the value. align=1: the data array is [N x i8] (byte-aligned), so a 16-byte
    # {i8*,i32,i8} string stored here is under-aligned; without align=1 LLVM emits an aligned
    # vector move that faults (SIGSEGV) on x86-64 (#145).
    value_llvm_type = value.type
    value_ptr_typed = codegen.builder.bitcast(data_ptr, ir.PointerType(value_llvm_type), name="value_ptr_typed")
    codegen.builder.store(value, value_ptr_typed, align=1)

    # Load the packed data back into the enum
    packed_data = codegen.builder.load(temp_alloca, name="packed_data")
    enum_value = codegen.builder.insert_value(enum_value, packed_data, 1, name="maybe_some_value")

    return enum_value


def emit_maybe_none(codegen: 'LLVMCodegen', value_type: Type) -> ir.Value:
    """Emit Maybe.None() constructor.

    Args:
        codegen: The LLVM codegen instance.
        value_type: The T type parameter for Maybe<T>.

    Returns:
        The constructed Maybe<T> enum value with None variant.
    """
    # Ensure Maybe<T> type exists
    maybe_enum = ensure_maybe_type_exists(codegen, value_type)
    if maybe_enum is None:
        raise_internal_error("CE0047", type=str(value_type))

    # Get the LLVM enum type: {i32 tag, [N x i8] data}
    llvm_enum_type = codegen.types.get_enum_type(maybe_enum)

    # Get None variant index (should be 1)
    none_index = maybe_enum.get_variant_index("None")

    # Create undefined enum value
    enum_value = ir.Constant(llvm_enum_type, ir.Undefined)

    # Set the tag (discriminant) for None variant
    tag = ir.Constant(codegen.types.i32, none_index)
    enum_value = codegen.builder.insert_value(enum_value, tag, 0, name="maybe_none_tag")

    # None variant has no associated data, so we just set an undefined data field
    data_array_type = llvm_enum_type.elements[1]  # [N x i8] array
    undef_data = ir.Constant(data_array_type, ir.Undefined)
    enum_value = codegen.builder.insert_value(enum_value, undef_data, 1, name="maybe_none_value")

    return enum_value
