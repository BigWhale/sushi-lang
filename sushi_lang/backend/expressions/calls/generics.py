"""Generic type method call handlers (Result, Maybe, Own, HashMap, List)."""
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
    """Try to emit a built-in Result<T, E> or Maybe<T> method."""
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
    from sushi_lang.backend.generics.own import emit_builtin_own_method

    method = expr.method
    if not is_builtin_own_method(method):
        return None

    receiver_semantic_type = infer_semantic_type(codegen, expr, None, "Own<", StructType)
    if not (isinstance(receiver_semantic_type, StructType)
            and receiver_semantic_type.name.startswith("Own<")):
        return None

    own_value = None if method == "alloc" else codegen.expressions.emit_expr(expr.receiver)
    temp_expr = MethodCall(receiver=expr.receiver, method=method, args=expr.args, loc=expr.loc)
    return emit_builtin_own_method(codegen, temp_expr, own_value, receiver_semantic_type)


def try_emit_hashmap_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as HashMap<K, V> method. Returns None if not a HashMap<K, V> method."""
    from sushi_lang.backend.generics.hashmap import is_builtin_hashmap_method, emit_hashmap_method
    from sushi_lang.backend.expressions.calls.utils import infer_semantic_type, emit_receiver_as_pointer

    method = expr.method
    if not is_builtin_hashmap_method(method):
        return None

    receiver_semantic_type = infer_semantic_type(codegen, expr, None, "HashMap<", StructType)
    if not (isinstance(receiver_semantic_type, StructType)
            and receiver_semantic_type.name.startswith("HashMap<")):
        return None

    # `HashMap.new()` is a static call: its receiver is the type name, not a value.
    # Every other method mutates or probes the table and wants a POINTER; a receiver
    # with no address (a call result) falls back to the value.
    if method == "new":
        receiver_value = None
    else:
        receiver_value = emit_receiver_as_pointer(
            codegen, expr.receiver, receiver_semantic_type)
        if receiver_value is None:
            receiver_value = codegen.expressions.emit_expr(expr.receiver)

    temp_expr = MethodCall(receiver=expr.receiver, method=method, args=expr.args, loc=expr.loc)
    return emit_hashmap_method(codegen, temp_expr, receiver_value, receiver_semantic_type, to_i1)


def try_emit_list_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as List<T> method. Returns None if not a List<T> method."""
    from sushi_lang.semantics.generics.list import is_builtin_list_method
    from sushi_lang.backend.expressions.calls.utils import infer_semantic_type, emit_receiver_as_pointer

    from sushi_lang.backend.generics.list import emit_list_method

    method = expr.method
    if not is_builtin_list_method(method):
        return None

    receiver_semantic_type = infer_semantic_type(codegen, expr, None, "List<", StructType)
    if not (isinstance(receiver_semantic_type, StructType)
            and receiver_semantic_type.name.startswith("List<")):
        return None

    # `List.new()` / `List.with_capacity()` are static calls: the receiver is the type
    # name, not a value. Every other method wants a POINTER so it can mutate; a receiver
    # with no address (a call result) falls back to the value.
    if method in ("new", "with_capacity"):
        receiver_value = None
    else:
        receiver_value = emit_receiver_as_pointer(
            codegen, expr.receiver, receiver_semantic_type)
        if receiver_value is None:
            receiver_value = codegen.expressions.emit_expr(expr.receiver)

    temp_expr = MethodCall(receiver=expr.receiver, method=method, args=expr.args, loc=expr.loc)
    return emit_list_method(codegen, temp_expr, receiver_value, receiver_semantic_type, to_i1)
