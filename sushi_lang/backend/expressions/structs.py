"""Struct operations for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from sushi_lang.semantics.ast import (
    Expr, Name, Call, MemberAccess, MethodCall, DotCall, DynamicArrayNew, DynamicArrayFrom,
    IndexAccess,
)
from sushi_lang.semantics.typesys import (
    UnknownType, StructType, ArrayType, DynamicArrayType, ReferenceType,
)
from sushi_lang.backend.expressions.names import resolve_name_semantic_type
from sushi_lang.backend.ownership import ConsumingUse, consume
from sushi_lang.internals.errors import InternalCompilerError, raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_struct_constructor(codegen: 'LLVMCodegen', expr: Call, to_i1: bool = False) -> ir.Value:
    """Emit struct constructor call."""

    struct_name = expr.callee.id
    struct_type = codegen.struct_table.by_name[struct_name]

    llvm_struct_type = codegen.types.get_struct_type(struct_type)

    field_values = []
    for arg, (_field_name, field_type) in zip(expr.args, struct_type.fields, strict=True):
        if isinstance(field_type, DynamicArrayType):
            if isinstance(arg, DynamicArrayNew):
                element_llvm_type = codegen.types.ll_type(field_type.base_type)
                array_struct_type = ir.LiteralStructType([
                    codegen.types.i32,                     # len
                    codegen.types.i32,                     # cap
                    ir.PointerType(element_llvm_type)           # data*
                ])
                zero_i32 = ir.Constant(codegen.types.i32, 0)
                null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)
                array_struct = ir.Constant(array_struct_type, ir.Undefined)
                array_struct = codegen.builder.insert_value(array_struct, zero_i32, 0)
                array_struct = codegen.builder.insert_value(array_struct, zero_i32, 1)
                array_struct = codegen.builder.insert_value(array_struct, null_ptr, 2)
                field_values.append(array_struct)
            elif isinstance(arg, DynamicArrayFrom):
                # Create initialized dynamic array struct from array literal. A heap-owning
                # element that aliases a live owner is deep-copied so the struct field and
                # the source each own independent buffers (#139); a fresh temp is moved in.
                from sushi_lang.backend.types import arrays
                elements = arrays.emit_array_literal_elements(
                    codegen, arg.elements.elements, field_type.base_type
                )
                element_llvm_type = codegen.types.ll_type(field_type.base_type)
                array_struct = arrays.create_dynamic_array_from_elements(
                    codegen, field_type.base_type, element_llvm_type, elements
                )
                field_values.append(array_struct)
            else:
                arg_value = codegen.expressions.emit_expr(arg)

                if isinstance(arg_value.type, ir.PointerType):
                    element_llvm_type = codegen.types.ll_type(field_type.base_type)
                    expected_struct_type = ir.LiteralStructType([
                        codegen.types.i32,
                        codegen.types.i32,
                        ir.PointerType(element_llvm_type)
                    ])
                    if arg_value.type.pointee == expected_struct_type:
                        arg_value = codegen.builder.load(arg_value)

                field_values.append(consume(codegen, arg, arg_value, field_type,
                                            ConsumingUse.STRUCT_FIELD))
        else:
            arg_value = codegen.expressions.emit_expr(arg)

            resolved_field_type = field_type
            if isinstance(field_type, UnknownType):
                if field_type.name in codegen.struct_table.by_name:
                    resolved_field_type = codegen.struct_table.by_name[field_type.name]
                elif field_type.name in codegen.enum_table.by_name:
                    resolved_field_type = codegen.enum_table.by_name[field_type.name]

            # A constructor field takes ownership of its value. This was four
            # independent isinstance ladders -- one for owning structs, one for copy
            # composites, one for owning enums, one for `string` -- each with its own
            # spelling of "reads from a continuing owner", and NO arm at all for a fixed
            # `T[N]` field. The type class is the seam's business now.
            arg_value = consume(codegen, arg, arg_value, resolved_field_type,
                                ConsumingUse.STRUCT_FIELD)

            llvm_field_type = codegen.types.ll_type(field_type)
            casted_value = codegen.utils.cast_for_param(arg_value, llvm_field_type)
            field_values.append(casted_value)

    struct_value = ir.Constant(llvm_struct_type, ir.Undefined)

    for i, field_value in enumerate(field_values):
        struct_value = codegen.builder.insert_value(struct_value, field_value, i)

    return struct_value


def emit_member_access(codegen: 'LLVMCodegen', expr: MemberAccess, to_i1: bool = False) -> ir.Value:
    """Emit member access expression for struct fields."""
    struct_type = infer_struct_type(codegen, expr.receiver)

    field_index = struct_type.get_field_index(expr.member)
    if field_index is None:
        raise_internal_error("CE0029", struct=struct_type.name, field=expr.member)

    field_type = struct_type.get_field_type(expr.member)

    # Special handling for dynamic array fields: use GEP to get pointer to field
    # This enables method calls like c.numbers.push(10) to work (Rust-style)
    if isinstance(field_type, DynamicArrayType):
        struct_alloca = try_get_struct_alloca(codegen, expr.receiver)

        if struct_alloca is not None:
            from sushi_lang.backend import gep_utils
            field_ptr = gep_utils.gep_struct_field(
                codegen,
                struct_alloca,
                field_index,
                name=f"{expr.member}_ptr"
            )
            return field_ptr

    if isinstance(expr.receiver, Name):
        from sushi_lang.backend.expressions.type_utils import is_reference_parameter
        if is_reference_parameter(codegen, expr.receiver.id):
            slot = codegen.memory.find_local_slot(expr.receiver.id)
            struct_ptr = codegen.builder.load(slot, name=f"{expr.receiver.id}_ptr")
            receiver_value = codegen.builder.load(struct_ptr, name=f"{expr.receiver.id}_deref")
        else:
            receiver_value = codegen.expressions.emit_expr(expr.receiver)
    else:
        receiver_value = codegen.expressions.emit_expr(expr.receiver)

    field_value = codegen.builder.extract_value(receiver_value, field_index)
    return field_value


def try_get_struct_alloca(codegen: 'LLVMCodegen', receiver_expr: Expr) -> Optional[ir.Value]:
    """Try to get the alloca instruction or pointer for a struct variable."""
    if isinstance(receiver_expr, Name):
        slot = codegen.memory.try_find_local_slot(receiver_expr.id)
        if slot is None:
            return None

        from sushi_lang.backend.expressions.type_utils import is_reference_parameter
        if is_reference_parameter(codegen, receiver_expr.id):
            return codegen.builder.load(slot, name=f"{receiver_expr.id}_ptr")
        else:
            return slot
    elif isinstance(receiver_expr, MemberAccess):
        base_alloca = try_get_struct_alloca(codegen, receiver_expr.receiver)
        if base_alloca is None:
            return None

        parent_struct_type = infer_struct_type(codegen, receiver_expr.receiver)
        field_index = parent_struct_type.get_field_index(receiver_expr.member)
        if field_index is None:
            return None

        from sushi_lang.backend import gep_utils
        field_ptr = gep_utils.gep_struct_field(
            codegen,
            base_alloca,
            field_index,
            name=f"{receiver_expr.member}_ptr"
        )
        return field_ptr
    elif isinstance(receiver_expr, IndexAccess):
        # `a[i].field` -- GEP into the element rather than loading it (#187). Loading would
        # hand back a struct VALUE, and a dynamic-array field must be reached by ADDRESS:
        # `.len()`/`.push()` dispatch on the field's pointer, so a copy makes them fail.
        from sushi_lang.backend.types.arrays.indexing import emit_element_pointer
        return emit_element_pointer(codegen, receiver_expr)
    else:
        return None


def _resolve_to_struct(codegen: 'LLVMCodegen', ty) -> Optional[StructType]:
    """Resolve a semantic type to a concrete StructType, or None if it is not one."""
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if isinstance(ty, StructType):
        return ty
    if isinstance(ty, UnknownType):
        return codegen.struct_table.by_name.get(ty.name)
    if isinstance(ty, GenericTypeRef):
        type_args_str = ", ".join(str(arg) for arg in ty.type_args)
        return codegen.struct_table.by_name.get(f"{ty.base_name}<{type_args_str}>")
    return None


def _infer_call_struct(codegen: 'LLVMCodegen', expr: Expr) -> Optional[StructType]:
    """The struct a call receiver produces, or None."""
    return (_stamped_struct_type(codegen, expr)
            or _infer_get_element_struct(codegen, expr))


def _stamped_struct_type(codegen: 'LLVMCodegen', expr: Expr) -> Optional[StructType]:
    """The struct Pass 2 stamped as this call's return type, or None."""
    stamped = getattr(expr, "inferred_return_type", None)
    if stamped is None:
        return None
    if isinstance(stamped, ReferenceType):
        stamped = stamped.referenced_type
    resolved = _resolve_to_struct(codegen, stamped)
    if resolved is None:
        return None
    # Never rebuild a named type -- the table is what a name means (#240).
    return codegen.struct_table.by_name.get(resolved.name, resolved)


def _infer_get_element_struct(codegen: 'LLVMCodegen',
                              expr: Expr) -> Optional[StructType]:
    """Struct type produced by a `.get()` call, or None if this is not one."""
    if getattr(expr, "method", None) != "get":
        return None

    receiver = expr.receiver

    receiver_type = None
    if isinstance(receiver, Name):
        receiver_type = codegen.memory.find_semantic_type(receiver.id)

    if isinstance(receiver_type, ReferenceType):
        receiver_type = receiver_type.referenced_type

    if isinstance(receiver_type, DynamicArrayType):
        element_struct = _resolve_to_struct(codegen, receiver_type.base_type)
        if element_struct is not None:
            return element_struct
        if isinstance(receiver_type.base_type, UnknownType):
            raise_internal_error("CE0020", type=receiver_type.base_type.name)
        raise_internal_error("CE0043", type=str(receiver_type.base_type))

    own_struct = _resolve_to_struct(codegen, receiver_type)
    if own_struct is None:
        try:
            own_struct = infer_struct_type(codegen, receiver)
        except InternalCompilerError:
            return None

    if own_struct.name.startswith("Own<"):
        from sushi_lang.semantics.generics.own import get_own_element_type
        return _resolve_to_struct(codegen, get_own_element_type(own_struct))

    return None


def infer_struct_type(codegen: 'LLVMCodegen', expr: Expr) -> StructType:
    """Infer the struct type of an expression."""
    if isinstance(expr, Name):
        # Scope-aware, because `codegen.variable_types` is FLAT: a shadowing match binding
        # overwrote the outer entry for the rest of the function, so the outer struct was
        # read through the inner type's field indices -- silent wrong data, not a crash.
        var_name = expr.id
        var_type = resolve_name_semantic_type(codegen, var_name)
        if var_type is None:
            raise_internal_error("CE0056", name=var_name)

        if isinstance(var_type, ReferenceType):
            var_type = var_type.referenced_type

        if isinstance(var_type, UnknownType):
            if var_type.name not in codegen.struct_table.by_name:
                raise_internal_error("CE0020", type=var_type.name)
            return codegen.struct_table.by_name[var_type.name]
        elif isinstance(var_type, StructType):
            return var_type
        else:
            from sushi_lang.semantics.generics.types import GenericTypeRef
            if isinstance(var_type, GenericTypeRef):
                type_args_str = ", ".join(str(arg) for arg in var_type.type_args)
                struct_name = f"{var_type.base_name}<{type_args_str}>"
                if struct_name in codegen.struct_table.by_name:
                    return codegen.struct_table.by_name[struct_name]

            raise_internal_error("CE0031", type=str(var_type))

    elif isinstance(expr, MemberAccess):
        parent_struct_type = infer_struct_type(codegen, expr.receiver)
        field_type = parent_struct_type.get_field_type(expr.member)

        if field_type is None:
            raise_internal_error("CE0029", struct=parent_struct_type.name, field=expr.member)

        if isinstance(field_type, UnknownType):
            if field_type.name not in codegen.struct_table.by_name:
                raise_internal_error("CE0020", type=field_type.name)
            return codegen.struct_table.by_name[field_type.name]
        elif isinstance(field_type, StructType):
            return field_type
        else:
            from sushi_lang.semantics.generics.types import GenericTypeRef
            if isinstance(field_type, GenericTypeRef):
                type_args_str = ", ".join(str(arg) for arg in field_type.type_args)
                struct_name = f"{field_type.base_name}<{type_args_str}>"
                if struct_name in codegen.struct_table.by_name:
                    return codegen.struct_table.by_name[struct_name]

            raise_internal_error("CE0044", type=str(field_type))

    elif isinstance(expr, MethodCall):
        inferred = _infer_call_struct(codegen, expr)
        if inferred is not None:
            return inferred

        raise_internal_error("CE0068", method=expr.method)

    elif isinstance(expr, DotCall):
        inferred = _infer_call_struct(codegen, expr)
        if inferred is not None:
            return inferred

        raise_internal_error("CE0069", method=expr.method)

    elif isinstance(expr, IndexAccess):
        # `a[i].field` -- the struct is the indexed array's ELEMENT type. Pass 2 already
        # stamped it (#348), so read the stamp first; the structural walk below stays as
        # the answer for an unstamped node.
        from sushi_lang.backend.expressions.calls.utils import stamped_semantic_type
        stamped = stamped_semantic_type(codegen, expr)
        if isinstance(stamped, StructType):
            return stamped

        array_type = _indexed_array_type(codegen, expr.array)
        if isinstance(array_type, (ArrayType, DynamicArrayType)):
            return _resolve_struct_type(codegen, array_type.base_type, "CE0043")
        raise_internal_error("CE0043", type=str(array_type))

    else:
        raise_internal_error("CE0067", expr=type(expr).__name__)


def _indexed_array_type(codegen: 'LLVMCodegen', array_expr: Expr):
    """Semantic type of the array being indexed (a local, or a struct field)."""
    if isinstance(array_expr, Name):
        array_type = codegen.memory.find_semantic_type(array_expr.id)
        if isinstance(array_type, ReferenceType):
            array_type = array_type.referenced_type
        return array_type
    if isinstance(array_expr, MemberAccess):
        parent = infer_struct_type(codegen, array_expr.receiver)
        return parent.get_field_type(array_expr.member)
    return None


def _resolve_struct_type(codegen: 'LLVMCodegen', ty, err_code: str) -> StructType:
    """Resolve a declared type to the concrete StructType it names."""
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if isinstance(ty, StructType):
        return ty
    if isinstance(ty, UnknownType):
        if ty.name not in codegen.struct_table.by_name:
            raise_internal_error("CE0020", type=ty.name)
        return codegen.struct_table.by_name[ty.name]
    if isinstance(ty, GenericTypeRef):
        type_args_str = ", ".join(str(arg) for arg in ty.type_args)
        struct_name = f"{ty.base_name}<{type_args_str}>"
        if struct_name in codegen.struct_table.by_name:
            return codegen.struct_table.by_name[struct_name]
    raise_internal_error(err_code, type=str(ty))
