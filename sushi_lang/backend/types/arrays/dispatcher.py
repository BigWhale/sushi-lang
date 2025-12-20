"""
Array method dispatcher.

This module handles dispatching of built-in array method calls for both
fixed and dynamic arrays. It serves as the central entry point for all
array method calls from the expression emission layer.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import MethodCall, IndexAccess
from sushi_lang.semantics.typesys import DynamicArrayType, Type
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

from .methods import core, iterators, hashing


def is_builtin_array_method(method_name: str) -> bool:
    """Check if a method name is a built-in array method.

    Arrays are a CORE language feature, not stdlib.

    Args:
        method_name: The method name to check.

    Returns:
        True if the method is a built-in array method.
    """
    # Fixed array methods: len, get, iter, hash, fill, reverse
    # Dynamic array methods: len, get, push, pop, capacity, destroy, free, iter, clone, hash, fill, reverse
    # u8[] specific methods: to_string
    return method_name in {
        "len", "get", "push", "pop", "capacity", "destroy", "free",
        "iter", "to_string", "clone", "hash", "fill", "reverse"
    }


def emit_array_method(
    codegen: 'LLVMCodegen',
    expr: MethodCall,
    receiver_value: ir.Value,
    receiver_type: ir.Type,
    semantic_type: 'Type',
    to_i1: bool
) -> ir.Value:
    """Emit LLVM IR for built-in array method calls.

    Arrays are a CORE language feature that use inline emission.
    This function dispatches to specialized emitters based on method name
    and array type (fixed vs dynamic).

    Args:
        codegen: The LLVM code generator.
        expr: The method call expression.
        receiver_value: The LLVM value of the array.
        receiver_type: The LLVM type of the array.
        semantic_type: The semantic type of the array (DynamicArrayType or FixedArrayType).
        to_i1: Whether to convert result to i1.

    Returns:
        The result of the array method call.

    Raises:
        NotImplementedError: If the method is not implemented for the array type.
    """
    method_name = expr.method

    # Fixed array methods
    if isinstance(receiver_type, ir.ArrayType):
        match method_name:
            case "len":
                # Fixed array len: return array size as constant
                array_size = receiver_type.count
                len_value = ir.Constant(codegen.types.i32, array_size)
                return codegen.utils.as_i1(len_value) if to_i1 else len_value

            case "get":
                # Fixed array get: return Maybe<T> with bounds checking
                from .methods.safe_access import emit_fixed_array_get_maybe
                index_arg = expr.args[0]
                index_value = codegen.expressions.emit_expr(index_arg)
                if index_value.type != codegen.types.i32:
                    is_signed = index_value.type in (codegen.types.i8, codegen.types.i16, codegen.types.i64)
                    index_value = codegen.utils.convert_int_to_i32(index_value, is_signed=is_signed)
                return emit_fixed_array_get_maybe(codegen, receiver_value, receiver_type, index_value, semantic_type, to_i1)

            case "iter":
                # Fixed array iter: create iterator
                return iterators.emit_fixed_array_iter(codegen, expr, receiver_value, receiver_type, to_i1)

            case "hash":
                # Fixed array hash: compute hash of all elements
                return hashing.emit_fixed_array_hash_direct(codegen, expr, receiver_value, receiver_type, to_i1)

            case "fill":
                # Fixed array fill: fill all elements with a value
                # Need to get pointer to the array variable for in-place modification
                from sushi_lang.semantics.ast import Name
                if isinstance(expr.receiver, Name):
                    array_ptr = codegen.memory.find_local_slot(expr.receiver.id)
                else:
                    # For other cases, allocate temp and store
                    array_ptr = codegen.builder.alloca(receiver_type, name="temp_array")
                    codegen.builder.store(receiver_value, array_ptr)
                fill_value = codegen.expressions.emit_expr(expr.args[0])
                return core.emit_fixed_array_fill(codegen, array_ptr, receiver_type, fill_value)

            case "reverse":
                # Fixed array reverse: reverse in-place
                # Need to get pointer to the array variable for in-place modification
                from sushi_lang.semantics.ast import Name
                if isinstance(expr.receiver, Name):
                    array_ptr = codegen.memory.find_local_slot(expr.receiver.id)
                else:
                    # For other cases, allocate temp and store
                    array_ptr = codegen.builder.alloca(receiver_type, name="temp_array")
                    codegen.builder.store(receiver_value, array_ptr)
                return core.emit_fixed_array_reverse(codegen, array_ptr, receiver_type)

            case _:
                raise NotImplementedError(f"Fixed array method not implemented: {method_name}")

    # Dynamic array methods - unwrap pointer if needed
    if isinstance(receiver_type, ir.PointerType):
        array_struct_type = receiver_type.pointee
    else:
        array_struct_type = receiver_type

    match method_name:
        case "len":
            return core.emit_dynamic_array_len(codegen, receiver_value, to_i1)

        case "capacity":
            return core.emit_dynamic_array_capacity(codegen, receiver_value, to_i1)

        case "get":
            # Dynamic array get: return Maybe<T> with bounds checking
            from .methods.safe_access import emit_dynamic_array_get_maybe
            index_value = codegen.expressions.emit_expr(expr.args[0])
            # Cast index to i32 if it's a different integer type
            if index_value.type != codegen.types.i32:
                # Determine signedness based on type
                is_signed = index_value.type in (codegen.types.i8, codegen.types.i16, codegen.types.i64)
                index_value = codegen.utils.convert_int_to_i32(index_value, is_signed=is_signed)
            return emit_dynamic_array_get_maybe(codegen, receiver_value, array_struct_type, index_value, semantic_type, to_i1)

        case "push":
            element_value = codegen.expressions.emit_expr(expr.args[0])
            return core.emit_dynamic_array_push(codegen, receiver_value, array_struct_type, element_value)

        case "pop":
            return core.emit_dynamic_array_pop(codegen, receiver_value, array_struct_type, to_i1)

        case "free":
            # Extract element type from semantic type
            if isinstance(semantic_type, DynamicArrayType):
                element_semantic_type = semantic_type.base_type
            else:
                raise_internal_error("CE0042", type=type(semantic_type).__name__)
            return core.emit_dynamic_array_free(codegen, receiver_value, array_struct_type, element_semantic_type)

        case "destroy":
            return core.emit_dynamic_array_destroy(codegen, receiver_value, array_struct_type, semantic_type)

        case "iter":
            # Dynamic array iter: create iterator
            return iterators.emit_dynamic_array_iter(codegen, expr, receiver_value, array_struct_type, to_i1)

        case "clone":
            from .methods.transforms import emit_dynamic_array_clone
            return emit_dynamic_array_clone(codegen, expr, receiver_value, array_struct_type, to_i1)

        case "to_string":
            from .methods.transforms import emit_byte_array_to_string
            return emit_byte_array_to_string(codegen, expr, receiver_value, array_struct_type, to_i1)

        case "hash":
            # Dynamic array hash: compute hash of all elements
            return hashing.emit_dynamic_array_hash_direct(codegen, expr, receiver_value, array_struct_type, to_i1)

        case "fill":
            # Dynamic array fill: fill all elements with a value
            fill_value = codegen.expressions.emit_expr(expr.args[0])
            return core.emit_dynamic_array_fill(codegen, receiver_value, array_struct_type, fill_value)

        case "reverse":
            # Dynamic array reverse: reverse in-place
            return core.emit_dynamic_array_reverse(codegen, receiver_value, array_struct_type)

        case _:
            raise NotImplementedError(f"Dynamic array method not implemented: {method_name}")
