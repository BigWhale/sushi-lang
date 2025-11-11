"""
Variable lifecycle statement emission for the Sushi language compiler.

This module handles the generation of LLVM IR for variable declarations (let)
and variable rebinding (:=) with proper RAII cleanup and move semantics.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from internals.errors import raise_internal_error

if TYPE_CHECKING:
    from backend.interfaces import CodegenProtocol
    from semantics.ast import Let, Rebind


def emit_let(codegen: 'CodegenProtocol', stmt: 'Let') -> None:
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
    from semantics.typesys import DynamicArrayType, ArrayType, StructType, UnknownType
    from semantics.ast import ArrayLiteral

    blk = codegen.builder.block
    if blk.terminator is not None:
        raise_internal_error("CE0060")

    if stmt.ty is None:
        raise_internal_error("CE0015", message=f"let statement missing type information for '{{stmt.name}}'")

    # Track variable type for struct member access resolution
    codegen.variable_types[stmt.name] = stmt.ty

    # Special handling for dynamic array constructors - don't create slot here
    if isinstance(stmt.ty, DynamicArrayType):
        from backend.statements import initialization
        initialization.initialize_dynamic_array(codegen, stmt.name, stmt.ty, stmt.value)
    else:
        ll_type = codegen.types.ll_type(stmt.ty)

        # Resolve struct type name to actual StructType object for RAII tracking
        semantic_type = stmt.ty

        # Check if it's a StructType reference (not instantiated StructType from semantics)
        if isinstance(stmt.ty, StructType):
            semantic_type = stmt.ty
        elif isinstance(stmt.ty, UnknownType):
            # UnknownType has a name attribute - look it up in struct_table
            type_name = stmt.ty.name
            if type_name in codegen.struct_table.by_name:
                semantic_type = codegen.struct_table.by_name[type_name]
        elif isinstance(stmt.ty, str):
            if stmt.ty in codegen.struct_table.by_name:
                semantic_type = codegen.struct_table.by_name[stmt.ty]

        slot = codegen.memory.create_local_nostore(stmt.name, ll_type, semantic_type)

        # Register Own<T> variables for RAII cleanup
        if isinstance(semantic_type, StructType) and hasattr(codegen, 'dynamic_arrays'):
            if codegen.dynamic_arrays.is_own_type(semantic_type):
                codegen.dynamic_arrays.register_own(stmt.name, semantic_type)

        # Special handling for array literals
        if isinstance(stmt.ty, ArrayType) and isinstance(stmt.value, ArrayLiteral):
            from backend.statements import initialization
            initialization.initialize_array_literal(codegen, slot, stmt.value, ll_type)
        else:
            rhs = codegen.expressions.emit_expr(stmt.value)
            casted_rhs = codegen.utils.cast_for_param(rhs, ll_type)
            codegen.builder.store(casted_rhs, slot)


def emit_rebind(codegen: 'CodegenProtocol', stmt: 'Rebind') -> None:
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
    from semantics.ast import Name, MemberAccess
    from semantics.typesys import StructType, ReferenceType

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
    codegen: 'CodegenProtocol',
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
    from semantics.ast import Name

    # Extract variable name from target (must be Name for this function)
    if not isinstance(stmt.target, Name):
        raise_internal_error("CE0022", type=f"Expected Name target, got {type(stmt.target)}")
    var_name = stmt.target.id

    # Dynamic array rebind - need to clean up old array first to prevent memory leaks
    # Clean up the old array's memory before rebinding
    if hasattr(codegen, 'dynamic_arrays') and codegen.dynamic_arrays is not None:
        if var_name in codegen.dynamic_arrays.arrays:
            descriptor = codegen.dynamic_arrays.arrays[var_name]
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
            source_descriptor = codegen.dynamic_arrays.arrays[source_name]

            # Nullify the source array (set data=NULL, len=0, cap=0)
            source_slot = codegen.memory.find_local_slot(source_name)
            zero_i32 = ir.Constant(codegen.i32, 0)

            # Get element type from the source array
            element_type_llvm = dst.elements[2].pointee  # T* from {i32, i32, T*}
            null_ptr = ir.Constant(ir.PointerType(element_type_llvm), None)

            # Get pointers to source array fields using helper
            from backend.statements import utils
            len_ptr = utils.gep_struct_field(codegen, source_slot, 0, "len_ptr")
            cap_ptr = utils.gep_struct_field(codegen, source_slot, 1, "cap_ptr")
            data_ptr_ptr = utils.gep_struct_field(codegen, source_slot, 2, "data_ptr_ptr")

            # Nullify source array
            codegen.builder.store(zero_i32, len_ptr)
            codegen.builder.store(zero_i32, cap_ptr)
            codegen.builder.store(null_ptr, data_ptr_ptr)

            # Mark source as moved (prevents cleanup at scope exit)
            source_descriptor.moved = True


def _emit_struct_rebind(codegen: 'CodegenProtocol', stmt: 'Rebind', slot: 'ir.Value', val: 'ir.Value') -> None:
    """Emit rebinding for user-defined structs with cleanup.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The rebind statement.
        slot: The destination slot.
        val: The new value to store.
    """
    from semantics.typesys import StructType
    from semantics.ast import Name

    # Extract variable name from target (must be Name for this function)
    if not isinstance(stmt.target, Name):
        raise_internal_error("CE0022", type=f"Expected Name target, got {type(stmt.target)}")
    var_name = stmt.target.id

    # User-defined struct rebind - need to clean up old value first
    # Get the semantic type to check if cleanup is needed
    semantic_type = codegen.memory.find_semantic_type(var_name)

    if isinstance(semantic_type, StructType):
        # Check if this struct needs cleanup (has dynamic array fields)
        if hasattr(codegen, 'dynamic_arrays') and codegen.dynamic_arrays is not None:
            if codegen.dynamic_arrays.struct_needs_cleanup(semantic_type):
                # Clean up dynamic array fields in the old struct value before rebinding
                codegen.dynamic_arrays.emit_struct_field_cleanup(var_name, semantic_type, slot)

    # Store the new struct value
    codegen.builder.store(val, slot)


def _emit_field_rebind(codegen: 'CodegenProtocol', stmt: 'Rebind') -> None:
    """Emit field rebinding (obj.field := value).

    Gets a pointer to the struct field and stores the new value directly.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The rebind statement with MemberAccess target.
    """
    from llvmlite import ir
    from semantics.ast import MemberAccess, Name
    from semantics.typesys import StructType

    target = stmt.target
    if not isinstance(target, MemberAccess):
        raise_internal_error("CE0022", type=f"Expected MemberAccess, got {type(target)}")

    # Emit the value to store
    val = codegen.expressions.emit_expr(stmt.value)

    # Get the receiver's struct type
    from backend.expressions.structs import infer_struct_type
    struct_type = infer_struct_type(codegen, target.receiver)

    # Get the field index
    field_index = struct_type.get_field_index(target.member)
    if field_index is None:
        raise_internal_error("CE0029", struct=struct_type.name, field=target.member)

    # Get a pointer to the struct (either alloca or reference parameter pointer)
    from backend.expressions.structs import try_get_struct_alloca
    struct_ptr = try_get_struct_alloca(codegen, target.receiver)

    if struct_ptr is None:
        # Can't get struct pointer - this shouldn't happen after semantic analysis
        raise_internal_error("CE0022", type=f"Cannot get pointer for field rebinding")

    # Use GEP to get pointer to the specific field
    from backend import gep_utils
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
