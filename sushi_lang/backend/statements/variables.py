"""Variable lifecycle statement emission for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING
from sushi_lang.backend.destructors import destroy_old_value, resolve_named_type
from sushi_lang.backend.ownership import (
    ConsumingUse, bind, consume, relinquish, relinquish_temp)
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Let, Rebind
    from sushi_lang.semantics.typesys import Type


def emit_let(codegen: 'LLVMCodegen', stmt: 'Let') -> None:
    """Emit variable declaration with initialization."""
    from sushi_lang.semantics.typesys import DynamicArrayType, ArrayType
    from sushi_lang.semantics.ast import ArrayLiteral

    blk = codegen.builder.block
    if blk.terminator is not None:
        raise_internal_error("CE0060")

    if stmt.ty is None:
        raise_internal_error("CE0015", message=f"let statement missing type information for '{stmt.name}'")

    from sushi_lang.semantics.typesys import ReferenceType
    if isinstance(stmt.ty, ReferenceType):
        # `let poke T x = <place>` (#409): the slot holds a POINTER into storage the owner
        # keeps, exactly as a reference parameter's does, and is never registered for
        # cleanup -- the owner frees the pointee.
        from sushi_lang.backend.expressions.borrow import emit_place_address
        place_ptr = emit_place_address(codegen, stmt.value,
                                       writable=stmt.ty.is_poke())
        codegen.memory.create_local(stmt.name, place_ptr.type, place_ptr, stmt.ty,
                                    register_cleanup=False)
        return

    if isinstance(stmt.ty, DynamicArrayType):
        from sushi_lang.backend.statements import initialization
        initialization.initialize_dynamic_array(codegen, stmt.name, stmt.ty, stmt.value)
    else:
        ll_type = codegen.types.ll_type(stmt.ty)

        # Resolving a named type to its concrete EnumType is what lets an owning enum
        # local be registered for RAII cleanup; without it they leaked (#139).
        semantic_type = resolve_named_type(codegen, stmt.ty)

        # Registration is DEFERRED until the seam has spoken (#242): whether a `let` owns
        # its value is `bind()`'s answer, and that needs the initializer emitted first.
        slot = codegen.memory.create_local_nostore(stmt.name, ll_type, semantic_type,
                                                   register_cleanup=False)

        # Zero-initialise the slot BEFORE emitting the RHS: the RHS may contain a `??`
        # whose early exit sweeps every live local, and one store keeps the slot free of
        # poison for any path that reads it first (#145).
        from sushi_lang.semantics.typesys import BuiltinType as _BT
        from sushi_lang.semantics.typesys import FunctionType as _FT
        if semantic_type == _BT.STRING or isinstance(semantic_type, _FT):
            from llvmlite import ir as _ir
            codegen.builder.store(_ir.Constant(ll_type, None), slot)

        if isinstance(stmt.ty, ArrayType) and isinstance(stmt.value, ArrayLiteral):
            from sushi_lang.backend.statements import initialization
            initialization.initialize_array_literal(codegen, slot, stmt.value, ll_type,
                                                    stmt.ty.base_type)
            # An array literal is a fresh value that nothing else owns, so the binding
            # always owns it. Its ELEMENTS are the consuming use, and `initialize_array_literal`
            # routes each of them through the seam.
            owns = True
        else:
            rhs = codegen.expressions.emit_expr(stmt.value)
            rhs, owns = bind(codegen, stmt.value, rhs, semantic_type)
            casted_rhs = codegen.utils.cast_for_param(rhs, ll_type)
            codegen.builder.store(casted_rhs, slot)

        if owns:
            codegen.memory.register_owning_value(stmt.name, semantic_type, slot)

        if stmt.targets is not None:
            _emit_destructure(codegen, stmt.targets, slot, semantic_type, owns)
            if owns:
                relinquish_temp(codegen, stmt.name)


def _emit_destructure(codegen: 'LLVMCodegen', targets, whole_ptr: 'ir.Value',
                      whole_type: 'Type', owns: bool) -> None:
    """Split a tuple held at `whole_ptr` into its binders (docs/design/tuples.md).

    `owns` is the hidden whole's answer from `bind()`. An owned whole hands each element to
    its binder, which owns it from here, and destroys each `_` element now; the whole is
    then given up, so nothing is freed twice. A borrowed whole gives each binder a view
    that frees nothing.
    """
    from sushi_lang.backend import gep_utils
    from sushi_lang.backend.destructors import emit_value_destructor, needs_cleanup
    from sushi_lang.backend.utils.validation import require_builder
    from sushi_lang.internals.diagnostics import InternalCompilerError
    from sushi_lang.semantics.typesys import DynamicArrayType, StructType

    builder = require_builder(codegen)
    resolved = resolve_named_type(codegen, whole_type)
    if not isinstance(resolved, StructType):
        raise InternalCompilerError("CE0015", message="a destructure of a value that is "
                                                      "not a tuple reached the backend")
    for index, target in enumerate(targets):
        element_type = resolved.fields[index][1]
        field_ptr = gep_utils.gep_struct_field(codegen, whole_ptr, index,
                                               name=f"tuple_elem{index}")
        if target.nested is not None:
            _emit_destructure(codegen, target.nested, field_ptr, element_type, owns)
            continue
        if target.name is None:
            if owns and needs_cleanup(codegen, element_type):
                emit_value_destructor(codegen, field_ptr, element_type)
            continue
        bound_type = resolve_named_type(codegen, target.element_type or element_type)
        value = builder.load(field_ptr, name=target.name)
        if isinstance(bound_type, DynamicArrayType):
            arrays = codegen.dynamic_arrays
            if arrays is None:
                raise InternalCompilerError("CE0014")
            alloca = arrays.declare_dynamic_array(target.name, bound_type)
            codegen.memory.track_local(target.name, alloca, bound_type)
            builder.store(value, alloca)
            if not owns:
                relinquish(codegen, target.name)
            continue
        ll_type = codegen.types.ll_type(bound_type)
        slot = codegen.memory.create_local_nostore(target.name, ll_type, bound_type,
                                                   register_cleanup=False)
        builder.store(codegen.utils.cast_for_param(value, ll_type), slot)
        if owns:
            codegen.memory.register_owning_value(target.name, bound_type, slot)


def emit_rebind(codegen: 'LLVMCodegen', stmt: 'Rebind') -> None:
    """Emit variable or field rebinding (assignment to existing variable or struct field)."""
    from llvmlite import ir
    from sushi_lang.semantics.ast import IndexAccess, Name, MemberAccess
    from sushi_lang.semantics.typesys import ReferenceType

    from sushi_lang.backend.expressions.names import (
        namespaced_storage, resolve_name_slot, resolve_name_semantic_type)

    if isinstance(stmt.target, MemberAccess):
        storage = namespaced_storage(codegen, stmt.target)
        if storage is None:
            _emit_field_rebind(codegen, stmt)
            return
        # `geo.count := v`: the alias reaches a unit variable, so this is a rebind of
        # that storage and not a field write.
        var_name, slot, semantic_type = storage
    elif isinstance(stmt.target, IndexAccess):
        _emit_element_rebind(codegen, stmt)
        return
    elif isinstance(stmt.target, Name):
        var_name = stmt.target.id
        # A local's slot, or the global backing a unit variable (unit-storage.md).
        slot = resolve_name_slot(codegen, var_name)
        if slot is None:
            raise_internal_error("CE0055", name=var_name)
        # A local's type is in the scope manager; a unit variable's in the constant table.
        semantic_type = resolve_name_semantic_type(codegen, var_name)
    else:
        raise_internal_error("CE0022", type=f"Unsupported rebind target: {type(stmt.target)}")

    if _emit_string_append_rebind(codegen, stmt, slot, var_name, semantic_type):
        return

    # The descriptor BY VALUE, from every producer (docs/design/array-representation.md):
    # the load-if-pointer branch that stood here was reached by nothing once the two
    # producers that answered an address were fixed (cf12c73f), measured again for #553.
    val = codegen.expressions.emit_expr(stmt.value)

    if isinstance(semantic_type, ReferenceType):
        # A `poke` rebind stores THROUGH the pointer, so ownership applies at both ends:
        # the new value comes through the seam, and the OLD value is freed here, because
        # the caller frees only what the binding holds at scope exit. Returning before
        # either step double-freed a string (#303) and leaked an array (#304).
        #
        # The slot holds a POINTER and is never registered, so there is no move mark to
        # consult -- the pointee always belongs to the caller. `consume` runs BEFORE the
        # destructor: a source aliasing the value about to be freed must be read first.
        referent_type = semantic_type.referenced_type
        val = consume(codegen, stmt.value, val, referent_type, ConsumingUse.REBIND)
        ref_ptr = codegen.builder.load(slot, name=f"{var_name}_ref_ptr")
        destroy_old_value(codegen, ref_ptr, referent_type)
        codegen.builder.store(val, ref_ptr)
        return  # Done - skip the rest of the function

    dst = slot.type.pointee

    if isinstance(dst, ir.IntType):
        casted_value = codegen.utils.cast_to_int_width(val, dst)
        codegen.builder.store(casted_value, slot)
    elif isinstance(dst, (ir.FloatType, ir.DoubleType)):
        codegen.builder.store(val, slot)
    elif (isinstance(dst, ir.PointerType) and
          isinstance(dst.pointee, ir.IntType) and
          dst.pointee.width == 8):
        codegen.builder.store(val, slot)
    elif isinstance(dst, ir.types.BaseStructType):
        # BaseStructType, not LiteralStructType: a user struct is an IDENTIFIED type and a
        # sibling rather than a subclass, so the narrower check missed every one (#257).
        if codegen.types.is_dynamic_array_type(dst):
            _emit_dynamic_array_rebind(codegen, stmt, slot, val, dst, var_name, semantic_type)
        else:
            _emit_owned_value_rebind(codegen, stmt, slot, val, var_name, semantic_type)
    elif isinstance(dst, ir.ArrayType):
        # A fixed array follows the struct rule (#1082): a plain element type copies, and
        # an owning one moves through the seam after the old elements are destroyed.
        _emit_owned_value_rebind(codegen, stmt, slot, val, var_name, semantic_type)
    else:
        raise_internal_error("CE0022", type=str(dst))


def _emit_string_append_rebind(codegen: 'LLVMCodegen', stmt: 'Rebind', slot: 'ir.Value',
                               var_name: str, semantic_type: 'Type | None') -> bool:
    """`s := s.concat(x)`: grow the buffer in place, or answer False for the general path.

    The general path frees the old value right after the copy, so `string_append` hands
    the old buffer to `realloc` under the same rule: only when the `owned` bit is set.
    """
    from sushi_lang.backend.expressions.calls.stdlib.signatures import as_param_value
    from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg
    from sushi_lang.backend.functions import declare_stdlib_function
    from sushi_lang.semantics.ast import DotCall, MethodCall, Name
    from sushi_lang.semantics.typesys import BuiltinType, ReferenceType
    from sushi_lang.sushi_stdlib.src.signatures import Param

    value = stmt.value
    if not (isinstance(value, (DotCall, MethodCall)) and value.method == "concat"
            and isinstance(value.receiver, Name) and value.receiver.id == var_name
            and len(value.args) == 1):
        return False

    through_reference = isinstance(semantic_type, ReferenceType)
    referent = semantic_type.referenced_type if through_reference else semantic_type
    if referent != BuiltinType.STRING:
        return False
    if through_reference:
        address = codegen.builder.load(slot, name=f"{var_name}_ref_ptr")
    else:
        da = getattr(codegen, "dynamic_arrays", None)
        if not codegen.moves.owns_unconditionally(slot) or (
                da is not None and da.is_destroyed(var_name)):
            return False
        address = slot

    old = codegen.builder.load(address, name=f"{var_name}_old")
    piece = as_param_value(codegen, Param(BuiltinType.STRING),
                           emit_borrowed_arg(codegen, value.args[0]))
    append = declare_stdlib_function(codegen.module, "string_append", old.type,
                                     [old.type, old.type])
    codegen.builder.store(codegen.builder.call(append, [old, piece], name="appended"), address)
    return True


def _emit_dynamic_array_rebind(
    codegen: 'LLVMCodegen',
    stmt: 'Rebind',
    slot: 'ir.Value',
    val: 'ir.Value',
    dst: 'ir.LiteralStructType',
    var_name: str,
    semantic_type: 'Type | None',
) -> None:
    """Emit rebinding for dynamic arrays with move semantics."""
    from llvmlite import ir
    from sushi_lang.semantics.ast import Name

    # Decide (and perform) ownership BEFORE the old-array destructor runs below: a COPY
    # reads the source buffer, and a source aliasing the buffer about to be freed would
    # otherwise be a use-after-free.
    val = consume(codegen, stmt.value, val, semantic_type, ConsumingUse.REBIND)

    # Dynamic array rebind - need to clean up old array first to prevent memory leaks
    # Clean up the old array's memory before rebinding
    descriptor = None
    if hasattr(codegen, 'dynamic_arrays') and codegen.dynamic_arrays is not None:
        descriptor = codegen.dynamic_arrays._array(var_name)
        if descriptor is not None:
            if not descriptor.destroyed:
                codegen.dynamic_arrays.emit_array_destructor(var_name)
    if descriptor is None:
        # No descriptor is registered for a unit variable -- it is never destroyed at
        # scope exit -- so the old buffer is freed through its slot here.
        destroy_old_value(codegen, slot, semantic_type)

    codegen.builder.store(val, slot)
    # The binding is RE-INITIALIZED: it owns the new array, so scope exit frees it
    # even if the previous value had been moved away (F5, 2026-08-14) or explicitly
    # destroyed (#294) -- the descriptor's destroyed flag describes the OLD value.
    codegen.moves.unmark(slot)
    if hasattr(codegen, 'dynamic_arrays') and codegen.dynamic_arrays is not None:
        codegen.dynamic_arrays.reset_destroyed_on_rebind(var_name)

    # Nullify a MOVED source's descriptor, so a later read is observably empty rather
    # than a stale view of the buffer the target now owns. A copied or adopted source must
    # NOT be nullified, which is what `moves.is_moved` distinguishes.
    if isinstance(stmt.value, Name):
        source_name = stmt.value.id
        source_slot = codegen.memory.try_find_local_slot(source_name)
        if (source_name in codegen.dynamic_arrays.arrays and source_slot is not None
                and codegen.moves.is_moved(source_slot)):
            zero_i32 = ir.Constant(codegen.i32, 0)
            element_type_llvm = dst.elements[2].pointee  # T* from {i32, i32, T*}
            null_ptr = ir.Constant(ir.PointerType(element_type_llvm), None)

            from sushi_lang.backend import gep_utils
            len_ptr = gep_utils.gep_dynamic_array_len(codegen, source_slot)
            cap_ptr = gep_utils.gep_dynamic_array_cap(codegen, source_slot)
            data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, source_slot, "data_ptr_ptr")

            codegen.builder.store(zero_i32, len_ptr)
            codegen.builder.store(zero_i32, cap_ptr)
            codegen.builder.store(null_ptr, data_ptr_ptr)


def _emit_owned_value_rebind(codegen: 'LLVMCodegen', stmt: 'Rebind', slot: 'ir.Value',
                        val: 'ir.Value', var_name: str,
                        semantic_type: 'Type | None') -> None:
    """Emit rebinding for a struct, an enum or a fixed array, with cleanup."""
    from sushi_lang.backend.destructors import resolve_named_type

    # User-defined struct / enum rebind - free the OLD owning value before overwriting it.
    # The private name lookup that used to stand here answered for an `UnknownType` only;
    # `resolve_named_type` is the shared one and also resolves a generic spelling.
    resolved = resolve_named_type(codegen, semantic_type)

    # Destroy the old heap so it does not leak when overwritten (#139), but only when the
    # binding still OWNS it. A MOVED value belongs to its new owner, and a DESTROYED one
    # left the freed pointer in the slot -- freeing that again frees whatever `malloc` has
    # since handed to the NEW value (#294).
    da = getattr(codegen, "dynamic_arrays", None)
    already_destroyed = da is not None and da.is_destroyed(var_name)
    if not already_destroyed:
        codegen.moves.emit_free_unless_moved(
            slot, lambda: destroy_old_value(codegen, slot, resolved))

    # A rebind takes ownership of its RHS. Run this for EVERY resolved type, not only the
    # cleanup-needing composites above: the gate belongs to the destroy-the-old-value step,
    # not to the decision about the new one.
    val = consume(codegen, stmt.value, val, resolved, ConsumingUse.REBIND)

    codegen.builder.store(val, slot)
    # The binding is RE-INITIALIZED: it owns the new value, so scope exit frees it
    # even if the previous value had been moved away (F5, 2026-08-14) or explicitly
    # destroyed (#294) -- the descriptor's destroyed flag describes the OLD value.
    codegen.moves.unmark(slot)
    if da is not None:
        da.reset_destroyed_on_rebind(var_name)


def _emit_element_rebind(codegen: 'LLVMCodegen', stmt: 'Rebind') -> None:
    """Emit indexed assignment (arr[i] := value)."""
    from sushi_lang.semantics.ast import IndexAccess

    target = stmt.target
    if not isinstance(target, IndexAccess):
        raise_internal_error("CE0022", type=f"Expected IndexAccess, got {type(target)}")

    # The value FIRST, then the element address: a dynamic array can reallocate while
    # the value is emitted, which would leave an address taken earlier pointing into
    # the freed buffer.
    val = codegen.expressions.emit_expr(stmt.value)

    # The element type is the stamp the typecheck pass put on the target while inferring it. The
    # backend never re-derives it: a miss is a gap in the typecheck pass, not a user error.
    element_type = getattr(target, 'inferred_element_type', None)
    if element_type is None:
        raise_internal_error("CE0015", message="indexed assignment target carries no "
                                               "element type from the typecheck pass")

    val = consume(codegen, stmt.value, val, element_type, ConsumingUse.ELEMENT_ASSIGN)

    # Bounds-checked, and the one address the read side already knows how to take.
    from sushi_lang.backend.types.arrays.indexing import emit_element_pointer
    element_ptr = emit_element_pointer(codegen, target)

    # Free what the element held, or overwriting it leaks. AFTER the value is consumed,
    # so a source aliasing the buffer about to be freed has already been read.
    destroy_old_value(codegen, element_ptr, element_type)

    codegen.builder.store(
        codegen.utils.cast_for_param(val, element_ptr.type.pointee), element_ptr)


def _emit_field_rebind(codegen: 'LLVMCodegen', stmt: 'Rebind') -> None:
    """Emit field rebinding (obj.field := value)."""
    from llvmlite import ir
    from sushi_lang.semantics.ast import MemberAccess

    target = stmt.target
    if not isinstance(target, MemberAccess):
        raise_internal_error("CE0022", type=f"Expected MemberAccess, got {type(target)}")

    val = codegen.expressions.emit_expr(stmt.value)

    from sushi_lang.backend.expressions.structs import infer_struct_type
    struct_type = infer_struct_type(codegen, target.receiver)

    field_index = struct_type.get_field_index(target.member)
    if field_index is None:
        raise_internal_error("CE0029", struct=struct_type.name, field=target.member)

    # A field assignment takes ownership of the value, exactly as a `let` or a rebind
    # does. It was not a recognised position at all: a raw GEP and store, with no clone,
    # no move mark, and no destruction of the value the field already held.
    field_type = struct_type.get_field_type(target.member)
    val = consume(codegen, stmt.value, val, field_type, ConsumingUse.FIELD_ASSIGN)

    from sushi_lang.backend.expressions.structs import try_get_struct_alloca
    struct_ptr = try_get_struct_alloca(codegen, target.receiver)

    if struct_ptr is None:
        raise_internal_error("CE0022", type="Cannot get pointer for field rebinding")

    from sushi_lang.backend import gep_utils
    field_ptr = gep_utils.gep_struct_field(
        codegen,
        struct_ptr,
        field_index,
        name=f"{target.member}_rebind_ptr"
    )

    # Free what the field held, or overwriting it leaks. AFTER the value is consumed, so
    # a source aliasing the buffer about to be freed has already been read.
    from sushi_lang.backend.destructors import emit_value_destructor, needs_cleanup
    if field_type is not None and needs_cleanup(codegen, field_type):
        emit_value_destructor(codegen, field_ptr, field_type)

    dst_type = field_ptr.type.pointee
    if isinstance(dst_type, ir.IntType):
        val = codegen.utils.cast_to_int_width(val, dst_type)

    codegen.builder.store(val, field_ptr)
