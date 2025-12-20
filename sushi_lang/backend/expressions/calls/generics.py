"""
Generic type method call handlers (Result, Maybe, Own, HashMap, List).

This module contains dispatcher helpers for built-in generic types that require
special handling during code generation.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Union

from llvmlite import ir
from sushi_lang.semantics.ast import DotCall, MethodCall, Name, Expr
from sushi_lang.semantics.typesys import EnumType, StructType

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def try_emit_result_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as Result<T> method. Returns None if not a Result<T> method."""
    from sushi_lang.backend.generics.results import is_builtin_result_method
    from sushi_lang.backend.expressions.calls.utils import infer_semantic_type

    method = expr.method
    if not is_builtin_result_method(method):
        return None

    receiver = expr.receiver
    args = expr.args

    # Emit the receiver first to get its value
    result_value = codegen.expressions.emit_expr(receiver)

    # Try to infer the receiver's type through multiple strategies
    receiver_semantic_type = infer_semantic_type(codegen, expr, result_value, "Result<", EnumType)

    # If we found a Result<T> type, emit the method call
    if isinstance(receiver_semantic_type, EnumType) and receiver_semantic_type.name.startswith("Result<"):
        from sushi_lang.backend.generics.results import emit_builtin_result_method
        temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
        # Copy resolved_enum_type from original expr if it exists
        if hasattr(expr, 'resolved_enum_type'):
            temp_expr.resolved_enum_type = expr.resolved_enum_type
        return emit_builtin_result_method(codegen, temp_expr, result_value, receiver_semantic_type, to_i1)

    return None


def try_emit_maybe_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as Maybe<T> method. Returns None if not a Maybe<T> method."""
    from sushi_lang.backend.generics.maybe import is_builtin_maybe_method
    from sushi_lang.backend.expressions.calls.utils import infer_semantic_type

    method = expr.method
    if not is_builtin_maybe_method(method):
        return None

    receiver = expr.receiver
    args = expr.args

    # Emit the receiver first to get its value
    maybe_value = codegen.expressions.emit_expr(receiver)

    # Try to infer the receiver's type (same strategies as Result<T>)
    receiver_semantic_type = infer_semantic_type(codegen, expr, maybe_value, "Maybe<", EnumType)

    # If we found a Maybe<T> type, emit the method call
    if isinstance(receiver_semantic_type, EnumType) and receiver_semantic_type.name.startswith("Maybe<"):
        from sushi_lang.backend.generics.maybe import emit_builtin_maybe_method
        temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
        return emit_builtin_maybe_method(codegen, temp_expr, maybe_value, receiver_semantic_type, to_i1)

    return None


def try_emit_own_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as Own<T> method. Returns None if not an Own<T> method."""
    from sushi_lang.backend.generics.own import is_builtin_own_method
    from sushi_lang.backend.expressions.calls.utils import infer_semantic_type

    method = expr.method
    if not is_builtin_own_method(method):
        return None

    receiver = expr.receiver
    args = expr.args

    # For Own.alloc(), we don't emit the receiver (it's just the type name)
    if method == "alloc":
        receiver_semantic_type = infer_semantic_type(codegen, expr, None, "Own<", StructType)
        if isinstance(receiver_semantic_type, StructType) and receiver_semantic_type.name.startswith("Own<"):
            from sushi_lang.backend.generics.own import emit_builtin_own_method
            temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
            return emit_builtin_own_method(codegen, temp_expr, None, receiver_semantic_type)
    else:
        # For get() and destroy(), emit the receiver first
        own_value = codegen.expressions.emit_expr(receiver)
        receiver_semantic_type = infer_semantic_type(codegen, expr, own_value, "Own<", StructType)

        if isinstance(receiver_semantic_type, StructType) and receiver_semantic_type.name.startswith("Own<"):
            from sushi_lang.backend.generics.own import emit_builtin_own_method
            temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
            return emit_builtin_own_method(codegen, temp_expr, own_value, receiver_semantic_type)

    return None


def try_emit_hashmap_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as HashMap<K, V> method. Returns None if not a HashMap<K, V> method."""
    # Import from stdlib location
    from sushi_lang.sushi_stdlib.generics.collections.hashmap import is_builtin_hashmap_method, emit_hashmap_method
    from sushi_lang.backend.expressions.calls.utils import infer_semantic_type, emit_receiver_as_pointer

    method = expr.method
    if not is_builtin_hashmap_method(method):
        return None

    receiver = expr.receiver
    args = expr.args

    # For HashMap.new(), we don't emit the receiver (it's just the type name)
    if method == "new":
        receiver_semantic_type = infer_semantic_type(codegen, expr, None, "HashMap<", StructType)
        if isinstance(receiver_semantic_type, StructType) and receiver_semantic_type.name.startswith("HashMap<"):
            temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
            return emit_hashmap_method(codegen, temp_expr, None, receiver_semantic_type, to_i1)
    else:
        # For other methods, we need the HashMap as a POINTER for mutation
        hashmap_ptr = emit_receiver_as_pointer(codegen, receiver)
        receiver_semantic_type = infer_semantic_type(codegen, expr, None, "HashMap<", StructType)

        if hashmap_ptr is not None and isinstance(receiver_semantic_type, StructType) and receiver_semantic_type.name.startswith("HashMap<"):
            temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
            return emit_hashmap_method(codegen, temp_expr, hashmap_ptr, receiver_semantic_type, to_i1)
        elif hashmap_ptr is None:
            # For other receiver types, emit normally
            hashmap_value = codegen.expressions.emit_expr(receiver)
            receiver_semantic_type = infer_semantic_type(codegen, expr, hashmap_value, "HashMap<", StructType)
            if isinstance(receiver_semantic_type, StructType) and receiver_semantic_type.name.startswith("HashMap<"):
                temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
                return emit_hashmap_method(codegen, temp_expr, hashmap_value, receiver_semantic_type, to_i1)

    return None


def try_emit_list_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as List<T> method. Returns None if not a List<T> method."""
    from sushi_lang.backend.generics.list import is_builtin_list_method
    from sushi_lang.backend.expressions.calls.utils import infer_semantic_type, emit_receiver_as_pointer

    method = expr.method
    if not is_builtin_list_method(method):
        return None

    receiver = expr.receiver
    args = expr.args

    # For List.new() and List.with_capacity(), we don't emit the receiver (it's just the type name)
    if method in ("new", "with_capacity"):
        receiver_semantic_type = infer_semantic_type(codegen, expr, None, "List<", StructType)
        if isinstance(receiver_semantic_type, StructType) and receiver_semantic_type.name.startswith("List<"):
            from sushi_lang.backend.generics.list import emit_list_method
            temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
            return emit_list_method(codegen, temp_expr, None, receiver_semantic_type, to_i1)
    else:
        # For other methods, emit the list as a pointer for mutation
        list_ptr = emit_receiver_as_pointer(codegen, receiver)
        if list_ptr is not None:
            receiver_semantic_type = infer_semantic_type(codegen, expr, None, "List<", StructType)
            if isinstance(receiver_semantic_type, StructType) and receiver_semantic_type.name.startswith("List<"):
                from sushi_lang.backend.generics.list import emit_list_method
                temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
                return emit_list_method(codegen, temp_expr, list_ptr, receiver_semantic_type, to_i1)
        else:
            # For other receiver types, emit normally
            list_value = codegen.expressions.emit_expr(receiver)
            receiver_semantic_type = infer_semantic_type(codegen, expr, list_value, "List<", StructType)
            if isinstance(receiver_semantic_type, StructType) and receiver_semantic_type.name.startswith("List<"):
                from sushi_lang.backend.generics.list import emit_list_method
                temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
                return emit_list_method(codegen, temp_expr, list_value, receiver_semantic_type, to_i1)

    return None
