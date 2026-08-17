"""Built-in extension methods for List<T> generic struct."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType
import llvmlite.ir as ir

from sushi_lang.internals.errors import raise_internal_error

from .methods_simple import (
    emit_list_clone,
    emit_list_new,
    emit_list_with_capacity,
    emit_list_len,
    emit_list_capacity,
    emit_list_is_empty
)
from .methods_modify import (
    emit_list_push,
    emit_list_pop,
    emit_list_get,
    emit_list_clear,
    emit_list_insert,
    emit_list_remove
)
from .methods_capacity import (
    emit_list_reserve,
    emit_list_shrink_to_fit
)
from .methods_destroy import (
    emit_list_destroy,
    emit_list_free
)
from .methods_debug import (
    emit_list_debug
)
from .methods_iter import (
    emit_list_iter
)


def emit_list_method(
    codegen: Any,
    expr: MethodCall,
    receiver_value: ir.Value,
    receiver_type: StructType,
    to_i1: bool
) -> ir.Value:
    """Emit LLVM IR for List<T> method calls."""
    method = expr.method

    if method == "new":
        result = emit_list_new(codegen, receiver_type)
    elif method == "with_capacity":
        result = emit_list_with_capacity(codegen, expr, receiver_type)
    elif method == "len":
        result = emit_list_len(codegen, receiver_value)
    elif method == "capacity":
        result = emit_list_capacity(codegen, receiver_value)
    elif method == "is_empty":
        result = emit_list_is_empty(codegen, receiver_value)
    elif method == "push":
        result = emit_list_push(codegen, expr, receiver_value, receiver_type)
    elif method == "pop":
        result = emit_list_pop(codegen, receiver_value, receiver_type)
    elif method == "get":
        result = emit_list_get(codegen, expr, receiver_value, receiver_type)
    elif method == "clear":
        result = emit_list_clear(codegen, receiver_value, receiver_type)
    elif method == "insert":
        result = emit_list_insert(codegen, expr, receiver_value, receiver_type)
    elif method == "remove":
        result = emit_list_remove(codegen, expr, receiver_value, receiver_type)
    elif method == "reserve":
        result = emit_list_reserve(codegen, expr, receiver_value, receiver_type)
    elif method == "shrink_to_fit":
        result = emit_list_shrink_to_fit(codegen, receiver_value, receiver_type)
    elif method == "destroy":
        result = emit_list_destroy(codegen, receiver_value, receiver_type)
    elif method == "free":
        result = emit_list_free(codegen, receiver_value, receiver_type)
    elif method == "debug":
        result = emit_list_debug(codegen, receiver_value, receiver_type)
    elif method == "iter":
        result = emit_list_iter(codegen, expr, receiver_value, receiver_type)
    elif method == "clone":
        result = emit_list_clone(codegen, receiver_value, receiver_type)
    else:
        raise_internal_error("CE0083", method=method)

    if to_i1 and method == "is_empty":
        result = codegen.utils.as_i1(result)

    return result


