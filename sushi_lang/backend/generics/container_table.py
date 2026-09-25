"""The emitter table of a built-in container: one row per method name (#875).

Each container package (`List@(T)`, `HashMap@(K, V)`) builds one table at import, and its
dispatcher looks the method up here. The key set of each table is the key set of the
semantic arity table; `tests/unit/test_container_emitters_match_the_table.py` holds it.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, NamedTuple

import llvmlite.ir as ir

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.ast import DotCall, MethodCall
from sushi_lang.semantics.typesys import StructType


class ContainerMethod(NamedTuple):
    """One row of a container's emitter table: the emitter, and whether it answers a `bool`.

    Every emitter takes `(codegen, expr, receiver_value, receiver_type)`. A `bool` answer
    is converted to the shape the caller asks for (`to_i1`) after the call.
    """

    emit: Callable[[Any, Any, Any, Any], ir.Value]
    answers_bool: bool = False


def emit_from_table(
    table: Mapping[str, ContainerMethod],
    missing_code: str,
    codegen: Any,
    expr: MethodCall | DotCall,
    receiver_value: Any,
    receiver_type: StructType,
    to_i1: bool,
) -> ir.Value:
    """Look up the row of `expr.method` in `table`, call its emitter, and shape a `bool`."""
    row = table.get(expr.method)
    if row is None:
        raise_internal_error(missing_code, method=expr.method)
    result = row.emit(codegen, expr, receiver_value, receiver_type)
    if row.answers_bool:
        result = codegen.utils.bool_answer(result, to_i1)
    return result
