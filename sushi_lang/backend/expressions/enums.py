"""Enum constructor operations for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING, Union

from llvmlite import ir
from sushi_lang.semantics.ast import EnumConstructor, DotCall, Name
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import enum_utils

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import EnumType


def emit_enum_constructor(codegen: 'LLVMCodegen', expr: Union[EnumConstructor, DotCall], is_dotcall: bool = False) -> ir.Value:
    """Emit enum variant constructor (e.g., Result.Ok(42) or Color.Red())."""
    if is_dotcall:
        assert isinstance(expr.receiver, Name), "DotCall receiver must be a Name for enum constructors"
        enum_name = expr.receiver.id
        variant_name = expr.method
        args = expr.args
        resolved_enum_type = getattr(expr, 'resolved_enum_type', None)
    else:
        enum_name = expr.enum_name
        variant_name = expr.variant_name
        args = expr.args
        resolved_enum_type = expr.resolved_enum_type

    if resolved_enum_type is not None:
        return emit_enum_constructor_from_method_call(
            codegen, resolved_enum_type, variant_name, args
        )

    if enum_name not in codegen.enum_table.by_name:
        raise_internal_error("CE0033", name=enum_name)

    enum_type = codegen.enum_table.by_name[enum_name]

    return emit_enum_constructor_from_method_call(codegen, enum_type, variant_name, args)


def emit_enum_constructor_from_method_call(
    codegen: 'LLVMCodegen',
    enum_type: 'EnumType',
    variant_name: str,
    args: list
) -> ir.Value:
    """Emit enum constructor for method call syntax (e.g., Color.Red())."""

    variant_index = enum_type.get_variant_index(variant_name)
    if variant_index is None:
        raise_internal_error("CE0034", variant=variant_name, enum=enum_type.name)

    variant = enum_type.get_variant(variant_name)

    if len(args) != len(variant.associated_types):
        raise_internal_error("CE0096", operation=f"Variant {enum_type.name}.{variant_name} expects {len(variant.associated_types)} arguments, got {len(args)}"
        )

    llvm_enum_type = codegen.types.get_enum_type(enum_type)

    enum_value = enum_utils.construct_enum_variant(
        codegen, llvm_enum_type, variant_index,
        data=None, name_prefix=f"{enum_type.name}_{variant_name}"
    )

    if args:
        # Allocate temporary storage for the data. The [K x i64] member type makes the
        # alloca 8-aligned (#300 phase 2), so the naturally aligned field offsets below
        # are naturally aligned absolutely.
        data_array_type = llvm_enum_type.elements[1]  # [K x i64] array
        # ENTRY block, not the current position: constructing an enum inside a loop
        # would otherwise allocate again every iteration and never release it until
        # the function returned (BUGS.md B1). One slot per site is enough, because the
        # packed payload is loaded back out before the expression finishes.
        temp_alloca = codegen.memory.entry_alloca(data_array_type, "enum_data_temp")

        data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

        # Pack each argument at its offset from the ONE layout authority -- extraction,
        # destroy, clone and hash all read the same offsets, so construct and extract
        # cannot disagree (they used to derive offsets from two different size walks).
        field_offsets = codegen.types.payload_field_offsets(variant.associated_types)
        for i, (arg_expr, arg_type) in enumerate(zip(args, variant.associated_types, strict=True)):
            from sushi_lang.semantics.ast import DynamicArrayFrom
            from sushi_lang.semantics.typesys import DynamicArrayType

            if isinstance(arg_type, DynamicArrayType) and isinstance(arg_expr, DynamicArrayFrom):
                # An owning element that aliases a live local is deep-copied, or the enum
                # local and the source both free the shared buffer (#139). A fresh temp is
                # already the sole owner and moves in unchanged.
                from sushi_lang.backend.types import arrays
                elements = arrays.emit_array_literal_elements(
                    codegen, arg_expr.elements.elements, arg_type.base_type
                )
                element_llvm_type = codegen.types.ll_type(arg_type.base_type)
                arg_value = arrays.create_dynamic_array_from_elements(
                    codegen, arg_type.base_type, element_llvm_type, elements
                )
            else:
                arg_value = codegen.expressions.emit_expr(arg_expr)

                # Some expressions hand back a POINTER rather than the value -- an array
                # `.clone()`, for one. The payload goes into the variant's byte blob
                # verbatim, so an unnormalized pointer read back a garbage length.
                if (isinstance(arg_value.type, ir.PointerType)
                        and arg_value.type.pointee == codegen.types.ll_type(arg_type)):
                    arg_value = codegen.builder.load(arg_value, name="enum_payload_by_value")

                # An enum payload is stored SHALLOWLY into the variant's byte blob, so the
                # enum becomes an owner of whatever the value points at. Which is why the
                # decision matters here more than almost anywhere: a returned Result is
                # emitted BEFORE scope cleanup, so `return Result.Ok(w.items)` handed the
                # caller an already-freed buffer when this position got it wrong (#256).
                from sushi_lang.backend.ownership import ConsumingUse, consume
                arg_value = consume(codegen, arg_expr, arg_value, arg_type,
                                    ConsumingUse.ENUM_PAYLOAD)

            # Store the argument at its aligned offset. Natural alignment throughout:
            # the base is 8-aligned and the offset is naturally aligned (#300 phase 2),
            # so the packed-layout `align=1` workaround (#145) is gone.
            arg_llvm_type = arg_value.type
            arg_ptr_i8 = codegen.builder.gep(data_ptr, [ir.Constant(codegen.types.i32, field_offsets[i])], name=f"arg{i}_ptr")
            arg_ptr_typed = codegen.builder.bitcast(arg_ptr_i8, ir.PointerType(arg_llvm_type), name=f"arg{i}_ptr_typed")
            codegen.builder.store(arg_value, arg_ptr_typed)

        packed_data = codegen.builder.load(temp_alloca, name="packed_data")

        enum_value = enum_utils.set_enum_data(
            codegen, enum_value, packed_data,
            name=f"{enum_type.name}_{variant_name}_data"
        )

    return enum_value
