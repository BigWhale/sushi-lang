"""Generic type method call handlers (Result, Maybe, Own, HashMap, List)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional, Union

from llvmlite import ir
from sushi_lang.semantics.type_predicates import is_instance_of
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

    # Emit the receiver ONCE. Type inference may need the emitted value (its LLVM layout is the
    # last-resort strategy), so this cannot be deferred until after the family is known.
    receiver_value = codegen.expressions.emit_expr(receiver)

    if may_be_result:
        receiver_semantic_type = infer_semantic_type(codegen, expr, receiver_value, "Result", EnumType)
        if isinstance(receiver_semantic_type, EnumType) and is_instance_of(receiver_semantic_type, "Result"):
            from sushi_lang.backend.generics.results import emit_builtin_result_method
            emitted = emit_builtin_result_method(codegen, expr, receiver_value, receiver_semantic_type, to_i1)
            if method in TAG_ONLY_METHODS:
                destroy_enum_temp(codegen, receiver, receiver_value, receiver_semantic_type)
            return emitted

    if may_be_maybe:
        receiver_semantic_type = infer_semantic_type(codegen, expr, receiver_value, "Maybe", EnumType)
        if isinstance(receiver_semantic_type, EnumType) and is_instance_of(receiver_semantic_type, "Maybe"):
            from sushi_lang.backend.generics.maybe import emit_builtin_maybe_method
            emitted = emit_builtin_maybe_method(codegen, expr, receiver_value, receiver_semantic_type, to_i1)
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

    receiver_semantic_type = infer_semantic_type(codegen, expr, None, "Own", StructType)
    if not (isinstance(receiver_semantic_type, StructType)
            and is_instance_of(receiver_semantic_type, "Own")):
        return None

    own_value = None
    if method != "alloc":
        # `Own.alloc(x).get()` and `make()??.get()` name no storage, so nothing else frees
        # the box: the receiver needs an owner here or it leaks (#382). Every other
        # container asks the same question through its own receiver path.
        from sushi_lang.backend.expressions.memory import own_temporary
        own_value = codegen.expressions.emit_expr(expr.receiver)
        own_temporary(codegen, expr.receiver, own_value, receiver_semantic_type)
    return emit_builtin_own_method(codegen, expr, own_value, receiver_semantic_type)


def _try_emit_container_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall],
                                to_i1: bool, *, base: str, is_builtin: Callable[[str], bool],
                                emit: Callable) -> Optional[ir.Value]:
    """The ONE body behind the `HashMap@(K, V)` and `List@(T)` method emitters."""
    from sushi_lang.backend.expressions.calls.utils import infer_semantic_type, emit_receiver_as_pointer
    from sushi_lang.semantics.statics import is_builtin_static

    if not is_builtin(expr.method):
        return None

    receiver_semantic_type = infer_semantic_type(codegen, expr, None, base, StructType)
    if not (isinstance(receiver_semantic_type, StructType)
            and is_instance_of(receiver_semantic_type, base)):
        return None

    # A static (`List.new()`) has the type NAME as its receiver, not a value. Every
    # other method mutates or probes the container and wants a POINTER; a receiver
    # with no address (a call result) falls back to the value.
    if is_builtin_static(base, expr.method):
        receiver_value = None
    else:
        receiver_value = emit_receiver_as_pointer(
            codegen, expr.receiver, receiver_semantic_type)
        if receiver_value is None:
            receiver_value = codegen.expressions.emit_expr(expr.receiver)

    return emit(codegen, expr, receiver_value, receiver_semantic_type, to_i1)


def try_emit_hashmap_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as HashMap<K, V> method. Returns None if not a HashMap<K, V> method."""
    from sushi_lang.backend.generics.hashmap import is_builtin_hashmap_method, emit_hashmap_method
    return _try_emit_container_method(
        codegen, expr, to_i1, base="HashMap", is_builtin=is_builtin_hashmap_method,
        emit=emit_hashmap_method)


def try_emit_list_method(codegen: 'LLVMCodegen', expr: Union[MethodCall, DotCall], to_i1: bool) -> Optional[ir.Value]:
    """Try to emit as List<T> method. Returns None if not a List<T> method."""
    from sushi_lang.semantics.generics.list import is_builtin_list_method
    from sushi_lang.backend.generics.list import emit_list_method
    return _try_emit_container_method(
        codegen, expr, to_i1, base="List", is_builtin=is_builtin_list_method,
        emit=emit_list_method)
