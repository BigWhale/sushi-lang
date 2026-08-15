"""
Variable lifecycle statement emission for the Sushi language compiler.

This module handles the generation of LLVM IR for variable declarations (let)
and variable rebinding (:=) with proper RAII cleanup and move semantics.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from sushi_lang.backend.ownership import ConsumingUse, bind, consume
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Let, Rebind


def emit_let(codegen: 'LLVMCodegen', stmt: 'Let') -> None:
    """Emit variable declaration with initialization.

    Creates a local variable slot and initializes it with the provided expression.
    Handles type casting to match the declared type and special array initialization.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The let statement to emit.

    Raises:
        RuntimeError: If emitting after a terminator.
        TypeError: If the statement is missing type information.
    """
    from sushi_lang.semantics.typesys import DynamicArrayType, ArrayType, StructType, UnknownType
    from sushi_lang.semantics.ast import ArrayLiteral

    blk = codegen.builder.block
    if blk.terminator is not None:
        raise_internal_error("CE0060")

    if stmt.ty is None:
        raise_internal_error("CE0015", message=f"let statement missing type information for '{stmt.name}'")

    # Track variable type for struct member access resolution
    codegen.variable_types[stmt.name] = stmt.ty

    # Special handling for dynamic array constructors - don't create slot here
    if isinstance(stmt.ty, DynamicArrayType):
        from sushi_lang.backend.statements import initialization
        initialization.initialize_dynamic_array(codegen, stmt.name, stmt.ty, stmt.value)
    else:
        ll_type = codegen.types.ll_type(stmt.ty)

        # Resolve struct type name to actual StructType object for RAII tracking
        semantic_type = stmt.ty

        # Check if it's a StructType reference (not instantiated StructType from semantics)
        if isinstance(stmt.ty, StructType):
            semantic_type = stmt.ty
        elif isinstance(stmt.ty, UnknownType):
            # UnknownType has a name attribute - look it up in struct_table, then enum_table.
            # Resolving to a concrete EnumType lets create_local register the enum local for
            # RAII cleanup when it owns heap (a dynamic-array / string / ... variant payload);
            # #143 lifted CE2059 without this owner, so such enum locals leaked (#139).
            type_name = stmt.ty.name
            if type_name in codegen.struct_table.by_name:
                semantic_type = codegen.struct_table.by_name[type_name]
            elif type_name in codegen.enum_table.by_name:
                semantic_type = codegen.enum_table.by_name[type_name]
        elif isinstance(stmt.ty, str):
            if stmt.ty in codegen.struct_table.by_name:
                semantic_type = codegen.struct_table.by_name[stmt.ty]
            elif stmt.ty in codegen.enum_table.by_name:
                semantic_type = codegen.enum_table.by_name[stmt.ty]

        # Registration is DEFERRED until the ownership seam has spoken (#242). Whether a
        # `let` owns its value is `bind()`'s answer, and `bind()` cannot run until the
        # initializer is emitted. Registering first and undoing it afterwards is what the
        # two reconcilers used to do, and they derived the answer a second time to do it.
        slot = codegen.memory.create_local_nostore(stmt.name, ll_type, semantic_type,
                                                   register_cleanup=False)

        # Zero-initialise a string local's slot ({null, 0, owned=0}) BEFORE emitting the RHS,
        # and a closure local's fat pointer ({null fn, null env, null drop}) likewise. The RHS
        # may contain a `??` (`let checked = check(s)??`) whose early exit runs the string and
        # closure cleanup over every live local. This local is not registered yet, so that
        # sweep skips it -- but the store costs one instruction and it keeps the slot free of
        # poison for any path that reads it before the real value lands (#145).
        from sushi_lang.semantics.typesys import BuiltinType as _BT
        from sushi_lang.semantics.typesys import FunctionType as _FT
        if semantic_type == _BT.STRING or isinstance(semantic_type, _FT):
            from llvmlite import ir as _ir
            codegen.builder.store(_ir.Constant(ll_type, None), slot)

        # Special handling for array literals
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
            # A `let` BINDS. What that means for a given source is not this position's
            # decision to make -- see backend/ownership.py.
            rhs, owns = bind(codegen, stmt.value, rhs, semantic_type)
            casted_rhs = codegen.utils.cast_for_param(rhs, ll_type)
            codegen.builder.store(casted_rhs, slot)

        if owns:
            codegen.memory.register_local_cleanup(stmt.name, semantic_type, slot)
            # Own@(T) and List@(T) keep their own registries, which carry the element type
            # the destructor needs. `register_local_cleanup` does not reach them.
            if isinstance(semantic_type, StructType) and hasattr(codegen, 'dynamic_arrays'):
                if codegen.dynamic_arrays.is_own_type(semantic_type):
                    codegen.dynamic_arrays.register_own(stmt.name, semantic_type, slot)
                elif codegen.dynamic_arrays.is_list_type(semantic_type):
                    codegen.dynamic_arrays.register_list(stmt.name, semantic_type, slot)


def emit_rebind(codegen: 'LLVMCodegen', stmt: 'Rebind') -> None:
    """Emit variable or field rebinding (assignment to existing variable or struct field).

    Supports two forms:
    - Variable rebinding: x := value
    - Field rebinding: obj.field := value

    Implements Rust-style move semantics for dynamic array rebinding:
    - Variable-to-variable rebinding (arr1 := arr2) transfers ownership
    - Method call rebinding (arr := method()) works normally

    For structs with dynamic array fields, emits cleanup code for the
    old struct value before storing the new value to prevent memory leaks.

    For reference parameters, stores through the reference pointer to modify
    the caller's variable (implementing mutable reference semantics).

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The rebind statement to emit.

    Raises:
        TypeError: If the rebind target type is not supported.
    """
    from llvmlite import ir
    from sushi_lang.semantics.ast import Name, MemberAccess
    from sushi_lang.semantics.typesys import ReferenceType

    # Handle field rebinding (obj.field := value)
    if isinstance(stmt.target, MemberAccess):
        _emit_field_rebind(codegen, stmt)
        return

    # Handle simple variable rebinding (x := value)
    if not isinstance(stmt.target, Name):
        raise_internal_error("CE0022", type=f"Unsupported rebind target: {type(stmt.target)}")

    var_name = stmt.target.id
    slot = codegen.memory.find_local_slot(var_name)
    val = codegen.expressions.emit_expr(stmt.value)

    # Fix for method calls returning dynamic arrays: If val is a pointer to a dynamic array struct
    # (from methods like to_bytes() which return stack-allocated structs), load the struct value
    # This must be done BEFORE the reference check
    if isinstance(val.type, ir.PointerType) and codegen.types.is_dynamic_array_type(val.type.pointee):
        val = codegen.builder.load(val, name=f"{var_name}_rebind_value")

    # Check if this is a reference parameter (mutable reference)
    # For parameters, the type is in codegen.variable_types
    # For local variables, it's in memory.semantic_types
    semantic_type = codegen.variable_types.get(var_name) or codegen.memory.find_semantic_type(var_name)

    if isinstance(semantic_type, ReferenceType):
        # A `&poke` rebind stores THROUGH the pointer, so it overwrites a value the CALLER
        # owns. Ownership applies at both ends, exactly as it does for a local rebind:
        #
        #   1. the new value is taken from its source through the seam, and
        #   2. the OLD value is freed here, because the caller frees only what its binding
        #      holds at scope exit -- which is the new value.
        #
        # This arm used to store and return before both steps, so an owning pointee was
        # double-freed (a string, #303) or leaked (an array, #304).
        #
        # The slot itself holds a POINTER and is never registered for cleanup
        # (`functions/helpers.py`), so there is no move mark to consult on this path: the
        # pointee always belongs to the caller. `consume` runs BEFORE the destructor for
        # the reason `_emit_dynamic_array_rebind` states -- a source that aliases the
        # value about to be freed must be read first.
        referent_type = semantic_type.referenced_type
        val = consume(codegen, stmt.value, val, referent_type, ConsumingUse.REBIND)
        ref_ptr = codegen.builder.load(slot, name=f"{var_name}_ref_ptr")
        _destroy_old_value(codegen, ref_ptr, referent_type)
        codegen.builder.store(val, ref_ptr)
        return  # Done - skip the rest of the function

    dst = slot.type.pointee

    # Use centralized casting for integer types
    if isinstance(dst, ir.IntType):
        casted_value = codegen.utils.cast_to_int_width(val, dst)
        codegen.builder.store(casted_value, slot)
    elif (isinstance(dst, ir.PointerType) and
          isinstance(dst.pointee, ir.IntType) and
          dst.pointee.width == 8):
        codegen.builder.store(val, slot)
    elif isinstance(dst, ir.types.BaseStructType):
        # BaseStructType: a user struct is an identified type (#257), a sibling of
        # LiteralStructType rather than a subclass, so the narrower check sent every
        # user-struct rebind to the else branch below instead of _emit_struct_rebind.
        # Check if this is a dynamic array
        if codegen.types.is_dynamic_array_type(dst):
            _emit_dynamic_array_rebind(codegen, stmt, slot, val, dst)
        else:
            _emit_struct_rebind(codegen, stmt, slot, val)
    else:
        raise_internal_error("CE0022", type=str(dst))


def _destroy_old_value(codegen: 'LLVMCodegen', value_ptr: 'ir.Value', value_type) -> None:
    """Free the value that a rebind is about to overwrite.

    The ONE implementation of destroy-before-overwrite. Both rebind arms that store a
    whole value call it: the local arm (`_emit_struct_rebind`) and the reference arm.
    Two copies of this step is how #303 happened -- the reference arm simply did not
    have one.

    It carries no per-kind ladder. `emit_value_destructor` resolves a named type,
    dispatches a composite through the lifecycle handler table, guards a string on its
    runtime `owned` bit and a closure on its `drop_ptr`, so a literal-backed or
    non-capturing old value destroys to a runtime no-op.

    The CALLER decides whether the old value is still there to free, because the two
    arms answer that differently: a local can have moved its value away (the move mark),
    while a reference's pointee always belongs to the caller.

    Args:
        codegen: The main LLVMCodegen instance.
        value_ptr: Pointer to the value being overwritten.
        value_type: The semantic type of that value.
    """
    from sushi_lang.backend.destructors import (
        emit_value_destructor, needs_cleanup, resolve_named_type,
    )

    resolved = resolve_named_type(codegen, value_type)
    if needs_cleanup(resolved):
        emit_value_destructor(codegen, value_ptr, resolved)


def _emit_dynamic_array_rebind(
    codegen: 'LLVMCodegen',
    stmt: 'Rebind',
    slot: 'ir.Value',
    val: 'ir.Value',
    dst: 'ir.LiteralStructType'
) -> None:
    """Emit rebinding for dynamic arrays with move semantics.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The rebind statement.
        slot: The destination slot.
        val: The new value to store.
        dst: The destination type.
    """
    from llvmlite import ir
    from sushi_lang.semantics.ast import Name

    # Extract variable name from target (must be Name for this function)
    if not isinstance(stmt.target, Name):
        raise_internal_error("CE0022", type=f"Expected Name target, got {type(stmt.target)}")
    var_name = stmt.target.id

    # Decide (and perform) ownership BEFORE the old-array destructor runs below: a COPY
    # reads the source buffer, and a source aliasing the buffer about to be freed would
    # otherwise be a use-after-free.
    semantic_type = codegen.memory.find_semantic_type(var_name)
    val = consume(codegen, stmt.value, val, semantic_type, ConsumingUse.REBIND)

    # Dynamic array rebind - need to clean up old array first to prevent memory leaks
    # Clean up the old array's memory before rebinding
    if hasattr(codegen, 'dynamic_arrays') and codegen.dynamic_arrays is not None:
        descriptor = codegen.dynamic_arrays._array(var_name)
        if descriptor is not None:
            if not descriptor.destroyed:
                # Free the old array's memory
                codegen.dynamic_arrays._emit_array_destructor(var_name)
                # Don't mark as destroyed - we're rebinding to a new value

    # Store the new array value
    codegen.builder.store(val, slot)
    # The binding is RE-INITIALIZED: it owns the new array, so scope exit frees it
    # even if the previous value had been moved away (F5, 2026-08-14) or explicitly
    # destroyed (#294) -- the descriptor's destroyed flag describes the OLD value.
    codegen.moves.unmark(slot)
    if hasattr(codegen, 'dynamic_arrays') and codegen.dynamic_arrays is not None:
        codegen.dynamic_arrays.reset_destroyed_on_rebind(var_name)

    # Nullify a MOVED source's descriptor (data=NULL, len=0, cap=0). The move mark alone
    # keeps scope exit from freeing it; this additionally makes a later read of the source
    # observably empty rather than a stale view of the buffer the target now owns.
    # `consume` has already decided -- a copied or adopted source must NOT be nullified,
    # which is what `moves.is_moved` distinguishes.
    if isinstance(stmt.value, Name):
        source_name = stmt.value.id
        source_slot = codegen.memory.try_find_local_slot(source_name)
        if (source_name in codegen.dynamic_arrays.arrays and source_slot is not None
                and codegen.moves.is_moved(source_slot)):
            zero_i32 = ir.Constant(codegen.i32, 0)
            element_type_llvm = dst.elements[2].pointee  # T* from {i32, i32, T*}
            null_ptr = ir.Constant(ir.PointerType(element_type_llvm), None)

            from sushi_lang.backend import gep_utils
            len_ptr = gep_utils.gep_struct_field(codegen, source_slot, 0, "len_ptr")
            cap_ptr = gep_utils.gep_struct_field(codegen, source_slot, 1, "cap_ptr")
            data_ptr_ptr = gep_utils.gep_struct_field(codegen, source_slot, 2, "data_ptr_ptr")

            codegen.builder.store(zero_i32, len_ptr)
            codegen.builder.store(zero_i32, cap_ptr)
            codegen.builder.store(null_ptr, data_ptr_ptr)


def _emit_struct_rebind(codegen: 'LLVMCodegen', stmt: 'Rebind', slot: 'ir.Value', val: 'ir.Value') -> None:
    """Emit rebinding for user-defined structs with cleanup.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The rebind statement.
        slot: The destination slot.
        val: The new value to store.
    """
    from sushi_lang.backend.destructors import resolve_named_type
    from sushi_lang.semantics.ast import Name

    # Extract variable name from target (must be Name for this function)
    if not isinstance(stmt.target, Name):
        raise_internal_error("CE0022", type=f"Expected Name target, got {type(stmt.target)}")
    var_name = stmt.target.id

    # User-defined struct / enum rebind - free the OLD owning value before overwriting it.
    # The private name lookup that used to stand here answered for an `UnknownType` only;
    # `resolve_named_type` is the shared one and also resolves a generic spelling.
    resolved = resolve_named_type(codegen, codegen.memory.find_semantic_type(var_name))

    # Destroy the old value's heap so it does not leak when overwritten (#139) -- but
    # only when this binding still OWNS it. Two ways it may not:
    #
    #   MOVED     a moved-away value (`f(s); s := "new"`) belongs to its new owner, and
    #             freeing the stale copy here would double-free (F5).
    #   DESTROYED an explicit `.destroy()` already released it and did NOT null the field,
    #             so the slot still holds the freed pointer. Freeing it again frees
    #             whatever `malloc` has since handed to the NEW value -- which is what
    #             made `o.destroy(); o := Own.alloc(7); o.get()` read freed memory (#294).
    #
    # The per-kind ladder that used to stand here (struct/enum, then string, then closure)
    # is gone: every arm of it called the destructor this helper calls.
    da = getattr(codegen, "dynamic_arrays", None)
    already_destroyed = da is not None and da.is_destroyed(var_name)
    if not codegen.moves.is_moved(slot) and not already_destroyed:
        _destroy_old_value(codegen, slot, resolved)

    # A rebind takes ownership of its RHS. Run this for EVERY resolved type, not only the
    # cleanup-needing composites above: the gate belongs to the destroy-the-old-value step,
    # not to the decision about the new one.
    val = consume(codegen, stmt.value, val, resolved, ConsumingUse.REBIND)

    # Store the new value
    codegen.builder.store(val, slot)
    # The binding is RE-INITIALIZED: it owns the new value, so scope exit frees it
    # even if the previous value had been moved away (F5, 2026-08-14) or explicitly
    # destroyed (#294) -- the descriptor's destroyed flag describes the OLD value.
    codegen.moves.unmark(slot)
    if da is not None:
        da.reset_destroyed_on_rebind(var_name)


def _emit_field_rebind(codegen: 'LLVMCodegen', stmt: 'Rebind') -> None:
    """Emit field rebinding (obj.field := value).

    Gets a pointer to the struct field and stores the new value directly.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The rebind statement with MemberAccess target.
    """
    from llvmlite import ir
    from sushi_lang.semantics.ast import MemberAccess

    target = stmt.target
    if not isinstance(target, MemberAccess):
        raise_internal_error("CE0022", type=f"Expected MemberAccess, got {type(target)}")

    # Emit the value to store
    val = codegen.expressions.emit_expr(stmt.value)

    # Get the receiver's struct type
    from sushi_lang.backend.expressions.structs import infer_struct_type
    struct_type = infer_struct_type(codegen, target.receiver)

    # Get the field index
    field_index = struct_type.get_field_index(target.member)
    if field_index is None:
        raise_internal_error("CE0029", struct=struct_type.name, field=target.member)

    # A field assignment takes ownership of the value, exactly as a `let` or a rebind
    # does. It was not a recognised position at all: a raw GEP and store, with no clone,
    # no move mark, and no destruction of the value the field already held.
    field_type = struct_type.get_field_type(target.member)
    val = consume(codegen, stmt.value, val, field_type, ConsumingUse.FIELD_ASSIGN)

    # Get a pointer to the struct (either alloca or reference parameter pointer)
    from sushi_lang.backend.expressions.structs import try_get_struct_alloca
    struct_ptr = try_get_struct_alloca(codegen, target.receiver)

    if struct_ptr is None:
        # Can't get struct pointer - this shouldn't happen after semantic analysis
        raise_internal_error("CE0022", type="Cannot get pointer for field rebinding")

    # Use GEP to get pointer to the specific field
    from sushi_lang.backend import gep_utils
    field_ptr = gep_utils.gep_struct_field(
        codegen,
        struct_ptr,
        field_index,
        name=f"{target.member}_rebind_ptr"
    )

    # Free what the field already held, or overwriting it leaks. The variable rebind does
    # this (emit_struct_field_cleanup); the field rebind never did. Emitted AFTER the
    # value is consumed above, so a COPY has already read the source -- a source aliasing
    # the buffer about to be freed would otherwise be a use-after-free.
    from sushi_lang.backend.destructors import emit_value_destructor, needs_cleanup
    if field_type is not None and needs_cleanup(field_type):
        emit_value_destructor(codegen, field_ptr, field_type)

    # Cast the value if needed (for integer types)
    dst_type = field_ptr.type.pointee
    if isinstance(dst_type, ir.IntType):
        val = codegen.utils.cast_to_int_width(val, dst_type)

    # Store the new value directly to the field
    codegen.builder.store(val, field_ptr)
