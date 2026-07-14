"""
Generic type method call handlers (Result, Maybe, Own, HashMap, List).

This module contains dispatcher helpers for built-in generic types that require
special handling during code generation.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Union

from llvmlite import ir
from sushi_lang.semantics.ast import DotCall, MethodCall
from sushi_lang.semantics.typesys import EnumType, StructType

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


# Built-in Result/Maybe methods that read the discriminant tag and NEVER extract the payload.
# When their receiver is an unbound temporary, nothing else will ever free that payload, so the
# receiver is destroyed after the tag is read (#159). The extracting methods -- `realise`,
# `expect` -- are deliberately absent: they hand the payload to a new owner, and destroying the
# receiver as well would double-free it.
TAG_ONLY_METHODS = frozenset({"is_ok", "is_err", "is_some", "is_none"})


def try_emit_result_or_maybe_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit a built-in Result<T, E> or Maybe<T> method.

    Result and Maybe are handled together because `realise` and `expect` belong to BOTH
    built-in method sets. Dispatching them in two passes meant the Result pass emitted the
    receiver, discovered from its type that the receiver was a Maybe, declined -- and left the
    emitted IR stranded in the block, after which the Maybe pass emitted the receiver a second
    time. That duplicated the receiver's side effects and orphaned any heap it allocated
    (issue #199; the leak in issue #159's repro was the orphaned copy).

    The receiver's type, not the method's name, decides which family owns the call -- the same
    order Pass 2 uses (semantics/passes/types/calls/methods.py). So the receiver is emitted
    exactly ONCE here and the resulting value is reused for whichever family claims it.
    """
    from sushi_lang.semantics.generics.results import is_builtin_result_method
    from sushi_lang.semantics.generics.maybe import is_builtin_maybe_method
    from sushi_lang.backend.expressions.calls.utils import infer_semantic_type
    from sushi_lang.backend.expressions.memory import destroy_enum_temp

    method = expr.method
    may_be_result = is_builtin_result_method(method)
    may_be_maybe = is_builtin_maybe_method(method)
    if not (may_be_result or may_be_maybe):
        return None

    receiver = expr.receiver
    args = expr.args

    # Emit the receiver ONCE. Type inference may need the emitted value (its LLVM layout is the
    # last-resort strategy), so this cannot be deferred until after the family is known.
    receiver_value = codegen.expressions.emit_expr(receiver)

    if may_be_result:
        receiver_semantic_type = infer_semantic_type(codegen, expr, receiver_value, "Result<", EnumType)
        if isinstance(receiver_semantic_type, EnumType) and receiver_semantic_type.name.startswith("Result<"):
            from sushi_lang.backend.generics.results import emit_builtin_result_method
            temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
            # Copy resolved_enum_type from original expr if it exists
            if hasattr(expr, 'resolved_enum_type'):
                temp_expr.resolved_enum_type = expr.resolved_enum_type
            emitted = emit_builtin_result_method(codegen, temp_expr, receiver_value, receiver_semantic_type, to_i1)
            if method in TAG_ONLY_METHODS:
                destroy_enum_temp(codegen, receiver, receiver_value, receiver_semantic_type)
            return emitted

    if may_be_maybe:
        receiver_semantic_type = infer_semantic_type(codegen, expr, receiver_value, "Maybe<", EnumType)
        if isinstance(receiver_semantic_type, EnumType) and receiver_semantic_type.name.startswith("Maybe<"):
            from sushi_lang.backend.generics.maybe import emit_builtin_maybe_method
            temp_expr = MethodCall(receiver=receiver, method=method, args=args, loc=expr.loc)
            emitted = emit_builtin_maybe_method(codegen, temp_expr, receiver_value, receiver_semantic_type, to_i1)
            if method in TAG_ONLY_METHODS:
                destroy_enum_temp(codegen, receiver, receiver_value, receiver_semantic_type)
            return emitted

    return None


def try_emit_own_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as Own<T> method. Returns None if not an Own<T> method."""
    from sushi_lang.semantics.generics.own import is_builtin_own_method
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
    from sushi_lang.backend.generics.hashmap import is_builtin_hashmap_method, emit_hashmap_method
    from sushi_lang.backend.expressions.calls.utils import infer_semantic_type, emit_receiver_as_pointer

    method = expr.method
    if not is_builtin_hashmap_method(method):
        return None

    receiver = expr.receiver
    args = expr.args

    # HashMap.insert(k, v): BOTH the key and the value are stored shallowly, so the map takes
    # ownership of each and frees them on .free()/scope exit -- mark a bare-Name owning source
    # (string, array, List<T>, Own<T>, or heap-owning struct) moved so scope exit does not
    # double-free (#140). The key was previously left un-moved, so a heap-owning key (e.g. a
    # string bound out of a split() array) was freed by both its own RAII and the map (N1).
    if method == "insert" and len(args) >= 2:
        from sushi_lang.backend.expressions.memory import move_owning_arg_into_container
        move_owning_arg_into_container(codegen, args[0])
        move_owning_arg_into_container(codegen, args[1])

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
    from sushi_lang.semantics.generics.list import is_builtin_list_method
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
