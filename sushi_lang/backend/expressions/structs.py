"""Struct operations for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.generics.interned import interned_name
from sushi_lang.semantics.ast import (
    Expr, Name, Call, MemberAccess, MethodCall, DotCall, IndexAccess,
)
from sushi_lang.semantics.typesys import (
    UnknownType, StructType, ArrayType, DynamicArrayType, ReferenceType,
)
from sushi_lang.backend.expressions.names import resolve_name_semantic_type
from sushi_lang.backend.ownership import ConsumingUse, consume
from sushi_lang.internals.errors import raise_internal_error

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
            # A `from([...])` argument is emitted like any other expression: the typecheck
            # pass stamped the field's `T[]` on it, and the one emitter reads that stamp
            # (#544), so no derivation of the element type lives here.
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
    """Emit member access: a struct field, or a constant read through an alias."""
    ref = getattr(expr, 'namespace_ref', None)
    if ref is not None:
        from sushi_lang.backend.expressions.names import emit_namespaced_value
        return emit_namespaced_value(codegen, ref, to_i1)

    struct_type = infer_struct_type(codegen, expr.receiver)

    field_index = struct_type.get_field_index(expr.member)
    if field_index is None:
        raise_internal_error("CE0029", struct=struct_type.name, field=expr.member)

    field_type = struct_type.get_field_type(expr.member)

    # Special handling for dynamic array fields: use GEP to get pointer to field
    # This enables method calls like c.numbers.push(10) to work
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

    # A receiver nobody names still owns what it holds, and the field read is a borrow
    # out of it -- so without an owner here the whole struct leaks (#610). The twin of
    # `_own_receiver_temp` on the method-call side: `own_temporary` makes the decision,
    # and it gives no owner to a receiver that names storage somebody else frees.
    from sushi_lang.backend.expressions.memory import own_temporary
    own_temporary(codegen, expr.receiver, receiver_value, struct_type)

    field_value = codegen.builder.extract_value(receiver_value, field_index)
    return field_value


def try_get_struct_alloca(codegen: 'LLVMCodegen', receiver_expr: Expr) -> Optional[ir.Value]:
    """Try to get the alloca instruction or pointer for a struct variable."""
    if isinstance(receiver_expr, Name):
        # A local's slot, or the global backing a constant or a unit variable.
        from sushi_lang.backend.expressions.names import resolve_name_slot
        slot = resolve_name_slot(codegen, receiver_expr.id)
        if slot is None:
            return None

        from sushi_lang.backend.expressions.type_utils import is_reference_parameter
        if is_reference_parameter(codegen, receiver_expr.id):
            return codegen.builder.load(slot, name=f"{receiver_expr.id}_ptr")
        else:
            return slot
    elif isinstance(receiver_expr, MemberAccess):
        from sushi_lang.backend.expressions.names import namespaced_storage
        storage = namespaced_storage(codegen, receiver_expr)
        if storage is not None:
            return storage[1]  # `geo.pair.a := v` writes into the variable's storage
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


def _struct_named_by(codegen: 'LLVMCodegen', ty) -> Optional[StructType]:
    """The StructType a semantic type names, or None if it names no struct."""
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if isinstance(ty, StructType):
        return ty
    if isinstance(ty, UnknownType):
        return codegen.struct_table.by_name.get(ty.name)
    if isinstance(ty, GenericTypeRef):
        return codegen.struct_table.by_name.get(interned_name(ty.base_name, ty.type_args))
    return None


def _named_struct(codegen: 'LLVMCodegen', ty, strict: bool) -> Optional[StructType]:
    """The struct `ty` names, or None. A strict miss on an unknown name is CE0020.

    Every other strict miss raises the code of the caller, which knows the position.
    """
    found = _struct_named_by(codegen, ty)
    if found is None and strict and isinstance(ty, UnknownType):
        raise_internal_error("CE0020", type=ty.name)
    return found


def _stamped_struct_type(codegen: 'LLVMCodegen', expr: Expr) -> Optional[StructType]:
    """The struct the typecheck pass stamped on this expression, or None.

    `stamped_semantic_type` is the ONE reader of those stamps, and it knows which
    attribute each node kind carries: `inferred_unwrapped_type` for a `TryExpr`,
    `inferred_return_type` for a call, `inferred_element_type` for an index. This side
    only spells the answer as a struct.
    """
    from sushi_lang.backend.expressions.calls.utils import stamped_semantic_type

    stamped = stamped_semantic_type(codegen, expr)
    if isinstance(stamped, ReferenceType):
        stamped = stamped.referenced_type
    resolved = _struct_named_by(codegen, stamped)
    if resolved is None:
        return None
    # Never rebuild a named type -- the table is what a name means (#240).
    return codegen.struct_table.by_name.get(resolved.name, resolved)


def _infer_get_element_struct(codegen: 'LLVMCodegen', expr: Expr,
                              strict: bool) -> Optional[StructType]:
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
        found = _named_struct(codegen, receiver_type.base_type, strict)
        if found is None and strict:
            raise_internal_error("CE0043", type=str(receiver_type.base_type))
        return found

    own_struct = _struct_named_by(codegen, receiver_type)
    if own_struct is None:
        own_struct = try_infer_struct_type(codegen, receiver)

    if is_instance_of(own_struct, "Own"):
        from sushi_lang.semantics.generics.own import get_own_element_type
        return _struct_named_by(codegen, get_own_element_type(own_struct))

    return None


def _struct_type_of(codegen: 'LLVMCodegen', var_type, strict: bool) -> Optional[StructType]:
    """The struct a NAMED value's semantic type denotes, whichever spelling it kept."""
    if isinstance(var_type, ReferenceType):
        var_type = var_type.referenced_type
    found = _named_struct(codegen, var_type, strict)
    if found is None and strict:
        raise_internal_error("CE0031", type=str(var_type))
    return found


def try_infer_struct_type(codegen: 'LLVMCodegen', expr: Expr) -> Optional[StructType]:
    """The struct type of an expression, or None if the expression is not a struct.

    An alias name (`geo` in `geo.x`) and an enum type name (`Sign` in `Sign.Plus`) are
    receivers of that kind. The answer is None and no exception, so a caller never
    catches an internal error to mean "not a struct" (#823).
    """
    return _infer_struct(codegen, expr, strict=False)


def infer_struct_type(codegen: 'LLVMCodegen', expr: Expr) -> StructType:
    """Infer the struct type of an expression, or raise the internal error of the miss."""
    found = _infer_struct(codegen, expr, strict=True)
    if found is None:
        raise_internal_error("CE0067", expr=type(expr).__name__)
    return found


def _infer_struct(codegen: 'LLVMCodegen', expr: Expr, strict: bool) -> Optional[StructType]:
    """Infer the struct type of an expression.

    The typecheck pass's stamp answers first, whatever the node kind is. A dispatch of
    one hand-written arm per kind had no arm for a `TryExpr` or for a `Call`, so
    `make()??.x` and `Point(7).x` -- both legal -- stopped with the internal CE0067,
    which carries no location (#624). CE0067 is left for a node that has no stamp and
    that no arm below reconstructs.
    """
    stamped = _stamped_struct_type(codegen, expr)
    if stamped is not None:
        return stamped

    if isinstance(expr, Name):
        # Scope-aware, because `codegen.variable_types` is FLAT: a shadowing match binding
        # overwrote the outer entry for the rest of the function, so the outer struct was
        # read through the inner type's field indices -- silent wrong data, not a crash.
        var_name = expr.id
        var_type = resolve_name_semantic_type(codegen, var_name)
        if var_type is None:
            if strict:
                raise_internal_error("CE0056", name=var_name)
            return None
        return _struct_type_of(codegen, var_type, strict)

    elif isinstance(expr, MemberAccess):
        from sushi_lang.backend.expressions.names import namespaced_storage
        storage = namespaced_storage(codegen, expr)
        if storage is not None:
            # `geo.pair` names a unit variable behind an alias, not a field of `geo`.
            return _struct_type_of(codegen, storage[2], strict)
        parent_struct_type = _infer_struct(codegen, expr.receiver, strict)
        if parent_struct_type is None:
            return None
        field_type = parent_struct_type.get_field_type(expr.member)

        if field_type is None:
            if strict:
                raise_internal_error("CE0029", struct=parent_struct_type.name, field=expr.member)
            return None

        found = _named_struct(codegen, field_type, strict)
        if found is None and strict:
            raise_internal_error("CE0044", type=str(field_type))
        return found

    elif isinstance(expr, MethodCall):
        inferred = _infer_get_element_struct(codegen, expr, strict)
        if inferred is not None:
            return inferred

        if strict:
            raise_internal_error("CE0068", method=expr.method)
        return None

    elif isinstance(expr, DotCall):
        inferred = _infer_get_element_struct(codegen, expr, strict)
        if inferred is not None:
            return inferred

        if strict:
            raise_internal_error("CE0069", method=expr.method)
        return None

    elif isinstance(expr, IndexAccess):
        # `a[i].field` -- the struct is the indexed array's ELEMENT type, and the stamp
        # above already answered it (#348). This walk stays for an unstamped node.
        array_type = _indexed_array_type(codegen, expr.array, strict)
        if isinstance(array_type, (ArrayType, DynamicArrayType)):
            found = _named_struct(codegen, array_type.base_type, strict)
            if found is None and strict:
                raise_internal_error("CE0043", type=str(array_type.base_type))
            return found
        if strict:
            raise_internal_error("CE0043", type=str(array_type))
        return None

    else:
        if strict:
            raise_internal_error("CE0067", expr=type(expr).__name__)
        return None


def _indexed_array_type(codegen: 'LLVMCodegen', array_expr: Expr, strict: bool):
    """Semantic type of the array being indexed (a local, or a struct field)."""
    if isinstance(array_expr, Name):
        array_type = codegen.memory.find_semantic_type(array_expr.id)
        if isinstance(array_type, ReferenceType):
            array_type = array_type.referenced_type
        return array_type
    if isinstance(array_expr, MemberAccess):
        parent = _infer_struct(codegen, array_expr.receiver, strict)
        return None if parent is None else parent.get_field_type(array_expr.member)
    return None
