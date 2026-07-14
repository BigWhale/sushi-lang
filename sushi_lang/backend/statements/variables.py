"""
Variable lifecycle statement emission for the Sushi language compiler.

This module handles the generation of LLVM IR for variable declarations (let)
and variable rebinding (:=) with proper RAII cleanup and move semantics.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
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
        raise_internal_error("CE0015", message=f"let statement missing type information for '{{stmt.name}}'")

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

        slot = codegen.memory.create_local_nostore(stmt.name, ll_type, semantic_type)

        # Register Own<T> and List<T> variables for RAII cleanup
        if isinstance(semantic_type, StructType) and hasattr(codegen, 'dynamic_arrays'):
            if codegen.dynamic_arrays.is_own_type(semantic_type):
                # Own.get() yields a NON-owning borrow that aliases the container's
                # payload. Binding it must not create a second RAII owner, or the
                # container and the binding would both free the same pointer (#106).
                if getattr(stmt.value, 'method', None) != 'get':
                    codegen.dynamic_arrays.register_own(stmt.name, semantic_type, slot)
            elif codegen.dynamic_arrays.is_list_type(semantic_type):
                codegen.dynamic_arrays.register_list(stmt.name, semantic_type, slot)

        # Closure (function-value) ownership: create_local_nostore auto-registered this
        # local as an env owner. A capturing closure owns a heap env, so aliasing it must
        # keep exactly one owner (else double-free). Reconcile by binding shape:
        _reconcile_closure_ownership(codegen, stmt, semantic_type)

        # String ownership (#145): create_local_nostore auto-registered this string local
        # for owned-bit-guarded free. Reconcile by binding shape so exactly one owner frees
        # the heap buffer (else double-free / use-after-free).
        _reconcile_string_ownership(codegen, stmt, semantic_type)

        # Zero-initialise a string local's slot ({null, 0, owned=0}) BEFORE emitting the RHS.
        # The local is already registered for scope-exit free, but its RHS may contain a `??`
        # (e.g. `let checked = check(s)??`) whose early-exit emit_string_cleanup_all frees
        # every live string local -- including this one, whose slot is not yet stored. Without
        # zero-init the slot holds poison, so the guarded free reads a garbage owned byte and
        # may free a garbage/global pointer (SIGABRT). owned=0 makes that premature free a
        # no-op; the real value is stored just below (#145).
        from sushi_lang.semantics.typesys import BuiltinType as _BT
        if semantic_type == _BT.STRING and codegen.memory.is_string_registered(stmt.name):
            from llvmlite import ir as _ir
            codegen.builder.store(_ir.Constant(ll_type, None), slot)

        # Zero-initialise a closure (function-value) local's fat-pointer slot
        # ({null fn, null env, null drop}) BEFORE emitting the RHS -- the same
        # hazard as the string case above. The local is already registered for
        # scope-exit env cleanup, so a `??` in the RHS (`let g = fallible()??`)
        # whose early-exit path runs closure cleanup would load an unstored slot
        # and call a garbage drop_ptr (SIGBUS). A null drop_ptr makes that
        # premature cleanup a no-op; the real value is stored just below.
        from sushi_lang.semantics.typesys import FunctionType as _FT
        if isinstance(semantic_type, _FT) and codegen.memory.is_closure_registered(stmt.name):
            from llvmlite import ir as _ir
            codegen.builder.store(_ir.Constant(ll_type, None), slot)

        # Special handling for array literals
        if isinstance(stmt.ty, ArrayType) and isinstance(stmt.value, ArrayLiteral):
            from sushi_lang.backend.statements import initialization
            initialization.initialize_array_literal(codegen, slot, stmt.value, ll_type,
                                                    stmt.ty.base_type)
        else:
            rhs = codegen.expressions.emit_expr(stmt.value)
            # Owning-struct copy semantics (#60/#134/#147): a struct that owns heap memory
            # (a string / array / ... field) must get INDEPENDENT buffers when it is aliased
            # by value, or two scope-registered owners double-free at scope exit. A bare-Name
            # or struct-field-read alias is deep-copied here; a fresh RHS (constructor / call
            # return) or an already-cloned array get-out is a sole owner and left as-is.
            rhs = _clone_owning_struct_alias(codegen, stmt, rhs, semantic_type)
            casted_rhs = codegen.utils.cast_for_param(rhs, ll_type)
            codegen.builder.store(casted_rhs, slot)


def _clone_owning_struct_alias(codegen: 'LLVMCodegen', stmt: 'Let', rhs: 'ir.Value', semantic_type) -> 'ir.Value':
    """Deep-copy an owning-struct RHS when the binding aliases an existing owner.

    Structs are copy types (#60/#134): `let p2 = p` and `let inner = outer.field` must give
    the new binding its own heap buffers so each of the two registered owners frees exactly
    once (#147). Only a bare-Name alias or a struct-field read aliases an owner that stays
    live; a constructor, call return, or array `.get()`/index get-out (already deep-copied at
    the access site) is a fresh sole owner and is returned unchanged.
    """
    from sushi_lang.semantics.typesys import StructType, EnumType, UnknownType
    from sushi_lang.semantics.ast import Name, MemberAccess

    resolved = semantic_type
    if isinstance(resolved, UnknownType):
        resolved = (codegen.struct_table.by_name.get(resolved.name)
                    or codegen.enum_table.by_name.get(resolved.name)
                    or resolved)
    # An enum whose active variant owns heap is a copy type too (#139): `let g = e` must
    # deep-copy so g and e each own independent buffers and free once. A get-out
    # (`let e0 = es[0]` / `.get()??`) is already deep-copied at the access site and its RHS
    # is not a bare Name/MemberAccess, so it is left unchanged here (no double clone).
    if not isinstance(resolved, (StructType, EnumType)):
        return rhs
    if not codegen.dynamic_arrays.struct_needs_cleanup(resolved):
        return rhs
    if isinstance(stmt.value, (Name, MemberAccess)):
        from sushi_lang.backend.expressions.memory import emit_value_clone
        return emit_value_clone(codegen, rhs, resolved)
    return rhs


def _reconcile_closure_ownership(codegen: 'LLVMCodegen', stmt: 'Let', semantic_type) -> None:
    """Keep exactly one RAII owner for a function-value (closure) binding.

    `create_local_nostore` registered `stmt.name` as an env owner. That is correct only
    when the RHS produces a FRESH owned closure (a lambda literal, a call that transferred
    ownership on return, or a bare fn ref whose env is null). When the RHS aliases an
    environment that something ELSE already owns, a second owner would double-free the
    shared env. Reconcile by binding shape:

    - `let g = f` where f is a registered owning local -> MOVE: mark f moved so only g
      frees (mirrors dynamic-array/Own move-on-return).
    - `let g = f` where f is NOT a registered owner (a param, or an already-borrowed
      alias) -> BORROW: the real owner lives elsewhere, so g must not free.
    - `let g = fns.get(i)??` / `let g = s.handler` (container get-out / struct-field read)
      -> BORROW: the container/struct still owns the env (mirrors Own<T>.get()).

    Only capturing closures carry a non-null env/drop, so for a non-capturing value every
    branch is a harmless no-op (the guarded free of a null drop does nothing).
    """
    from sushi_lang.semantics.typesys import FunctionType
    from sushi_lang.semantics.ast import Name, MemberAccess, TryExpr

    if not isinstance(semantic_type, FunctionType):
        return

    value = stmt.value
    # Unwrap `expr??` so a get-out through error propagation is visible.
    if isinstance(value, TryExpr):
        value = value.expr

    if isinstance(value, Name):
        source = value.id
        if codegen.memory.is_closure_registered(source):
            # MOVE: transfer ownership source -> g. g keeps its registration.
            codegen.memory.mark_struct_as_moved(source)
        else:
            # BORROW: source is a param or an alias whose env is owned elsewhere.
            codegen.memory.unregister_closure_cleanup(stmt.name)
    elif isinstance(value, MemberAccess):
        # BORROW: reading a closure out of a struct field; the struct owns it.
        codegen.memory.unregister_closure_cleanup(stmt.name)
    elif getattr(value, 'method', None) == 'get':
        # BORROW: a container get-out (List/array `.get()`); the container owns it.
        codegen.memory.unregister_closure_cleanup(stmt.name)
    # else: lambda literal / call / fn ref -> g owns a fresh env; keep registration.


def _reconcile_string_ownership(codegen: 'LLVMCodegen', stmt: 'Let', semantic_type) -> None:
    """Keep exactly one RAII owner for a string binding (#145).

    `create_local_nostore` registered `stmt.name` for owned-bit-guarded free. That is correct
    only when the RHS produces a FRESH owned string (a string method / interpolation / call
    return / literal). When the RHS aliases a buffer that something ELSE already owns, a second
    owner would double-free. Reconcile by binding shape, mirroring closures:

    - `let s2 = s` where s is a registered owning local -> MOVE: mark s moved so only s2 frees.
    - `let s2 = s` where s is NOT registered (a param / already-borrowed alias) -> BORROW: the
      caller's binding owns the buffer, so s2 must not free (unregister it).
    - `let s2 = obj.field` (struct-field read) / `let s2 = c.get(i)??` (container get-out)
      -> BORROW: the struct/container still owns the buffer (unregister s2).
    - literal / method / interpolation / other call -> s2 owns a fresh string; keep it. A
      literal carries owned=0, so its eventual free is a runtime no-op.
    """
    from sushi_lang.semantics.typesys import BuiltinType
    from sushi_lang.semantics.ast import Name, MemberAccess, TryExpr

    if semantic_type != BuiltinType.STRING:
        return

    value = stmt.value
    if isinstance(value, TryExpr):
        value = value.expr

    if isinstance(value, Name):
        source = value.id
        if codegen.memory.is_string_registered(source):
            # MOVE: transfer ownership source -> s2. s2 keeps its registration.
            codegen.memory.mark_struct_as_moved(source)
        else:
            # BORROW: source is a param or an alias whose buffer is owned elsewhere.
            codegen.memory.unregister_string_cleanup(stmt.name)
    elif isinstance(value, MemberAccess):
        # BORROW: reading a string out of a struct field; the struct owns it.
        codegen.memory.unregister_string_cleanup(stmt.name)
    elif getattr(value, 'method', None) == 'get':
        # BORROW: a container get-out (List/array/HashMap `.get()`); the container owns it.
        codegen.memory.unregister_string_cleanup(stmt.name)
    # else: literal / string method / interpolation / call -> s2 owns a fresh string; keep it.


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
        # For reference parameters, the slot stores a pointer to the actual variable
        # We need to:
        # 1. Load the pointer from the slot (1st dereference)
        # 2. Store the new value through that pointer
        ref_ptr = codegen.builder.load(slot, name=f"{var_name}_ref_ptr")
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
    elif isinstance(dst, ir.LiteralStructType):
        # Check if this is a dynamic array
        if codegen.types.is_dynamic_array_type(dst):
            _emit_dynamic_array_rebind(codegen, stmt, slot, val, dst)
        else:
            _emit_struct_rebind(codegen, stmt, slot, val)
    else:
        raise_internal_error("CE0022", type=str(dst))


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

    # MOVE SEMANTICS: Check if source is a variable (not a method call)
    # If arr1 := arr2, we need to nullify arr2 and mark it as moved
    if isinstance(stmt.value, Name):
        source_name = stmt.value.id
        if source_name in codegen.dynamic_arrays.arrays:
            # Nullify the source array (set data=NULL, len=0, cap=0)
            source_slot = codegen.memory.find_local_slot(source_name)
            zero_i32 = ir.Constant(codegen.i32, 0)

            # Get element type from the source array
            element_type_llvm = dst.elements[2].pointee  # T* from {i32, i32, T*}
            null_ptr = ir.Constant(ir.PointerType(element_type_llvm), None)

            # Get pointers to source array fields using helper
            from sushi_lang.backend import gep_utils
            len_ptr = gep_utils.gep_struct_field(codegen, source_slot, 0, "len_ptr")
            cap_ptr = gep_utils.gep_struct_field(codegen, source_slot, 1, "cap_ptr")
            data_ptr_ptr = gep_utils.gep_struct_field(codegen, source_slot, 2, "data_ptr_ptr")

            # Nullify source array
            codegen.builder.store(zero_i32, len_ptr)
            codegen.builder.store(zero_i32, cap_ptr)
            codegen.builder.store(null_ptr, data_ptr_ptr)

            # Mark source as moved (prevents cleanup at scope exit)
            codegen.memory.mark_struct_as_moved(source_name)


def _emit_struct_rebind(codegen: 'LLVMCodegen', stmt: 'Rebind', slot: 'ir.Value', val: 'ir.Value') -> None:
    """Emit rebinding for user-defined structs with cleanup.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The rebind statement.
        slot: The destination slot.
        val: The new value to store.
    """
    from sushi_lang.semantics.typesys import StructType, EnumType, UnknownType
    from sushi_lang.semantics.ast import Name, MemberAccess

    # Extract variable name from target (must be Name for this function)
    if not isinstance(stmt.target, Name):
        raise_internal_error("CE0022", type=f"Expected Name target, got {type(stmt.target)}")
    var_name = stmt.target.id

    # User-defined struct / enum rebind - free the OLD owning value before overwriting it.
    semantic_type = codegen.memory.find_semantic_type(var_name)
    resolved = semantic_type
    if isinstance(resolved, UnknownType):
        resolved = (codegen.struct_table.by_name.get(resolved.name)
                    or codegen.enum_table.by_name.get(resolved.name)
                    or resolved)

    if (isinstance(resolved, (StructType, EnumType))
            and hasattr(codegen, 'dynamic_arrays') and codegen.dynamic_arrays is not None
            and codegen.dynamic_arrays.struct_needs_cleanup(resolved)):
        # Destroy the old value's heap so it does not leak when overwritten (#139).
        codegen.dynamic_arrays.emit_struct_field_cleanup(var_name, resolved, slot)
        # The new value must own independent buffers: a bare-Name / member alias is deep-copied
        # (the source stays a live owner), else the target and the source both free it.
        if isinstance(stmt.value, (Name, MemberAccess)):
            from sushi_lang.backend.expressions.memory import emit_value_clone
            val = emit_value_clone(codegen, val, resolved)

    # Store the new value
    codegen.builder.store(val, slot)


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

    # Get a pointer to the struct (either alloca or reference parameter pointer)
    from sushi_lang.backend.expressions.structs import try_get_struct_alloca
    struct_ptr = try_get_struct_alloca(codegen, target.receiver)

    if struct_ptr is None:
        # Can't get struct pointer - this shouldn't happen after semantic analysis
        raise_internal_error("CE0022", type=f"Cannot get pointer for field rebinding")

    # Use GEP to get pointer to the specific field
    from sushi_lang.backend import gep_utils
    field_ptr = gep_utils.gep_struct_field(
        codegen,
        struct_ptr,
        field_index,
        name=f"{target.member}_rebind_ptr"
    )

    # Cast the value if needed (for integer types)
    dst_type = field_ptr.type.pointee
    if isinstance(dst_type, ir.IntType):
        val = codegen.utils.cast_to_int_width(val, dst_type)

    # Store the new value directly to the field
    codegen.builder.store(val, field_ptr)
