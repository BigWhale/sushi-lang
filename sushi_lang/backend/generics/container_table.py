"""The emitter table of a built-in container: one row per method name (#875).

Each container package (`List@(T)`, `HashMap@(K, V)`) builds one table at import, and its
dispatcher looks the method up here. The key set of each table is the key set of the
semantic arity table; `tests/unit/test_container_emitters_match_the_table.py` holds it.

The rows take operands (ruling Q7): the SLIR emitter evaluates every operand first, and
the row gets the address of the receiver, the receiver type and the value of each argument
(`emit_with_operands`).
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

import llvmlite.ir as ir

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import BuiltinType, StructType


class ContainerMethod(NamedTuple):
    """One row of a container's emitter table: the emitter, whether it answers a `bool`,
    whether it is a static (`List.new`), which builds a value and has no receiver, and
    whether an argument that moves by address comes as the address of its value.

    A row takes `(codegen, receiver_address, receiver_type, argument_values)`; the receiver
    address of a static is None. A `bool` answer is an `i8` after the call.
    """

    emit: Callable[[Any, Any, Any, Any], ir.Value]
    answers_bool: bool = False
    static: bool = False
    by_address: bool = False


def blank(codegen: Any) -> ir.Value:
    """The value of `~`, the answer of a method that answers nothing."""
    return ir.Constant(codegen.types.ll_type(BuiltinType.BLANK), 0)


def emit_with_operands(table: Mapping[str, ContainerMethod], missing_code: str, codegen: Any,
                       name: str, address: ir.Value, receiver_type: StructType,
                       args: Sequence[ir.Value]) -> ir.Value:
    """The value of the method `name` on the container at `address`, with the values of
    its arguments. A `bool` is an `i8`. The name of a static, called on a value, answers
    `~` and builds nothing."""
    row = _row(table, missing_code, name)
    if row.static:
        return blank(codegen)
    result = row.emit(codegen, address, receiver_type, args)
    if row.answers_bool:
        result = codegen.utils.bool_answer(result, False)
    return result


def emit_static_with_operands(table: Mapping[str, ContainerMethod], missing_code: str,
                              codegen: Any, name: str, instance: StructType,
                              args: Sequence[ir.Value]) -> ir.Value:
    """The value that the static `name` builds, an `instance`, from the values of its
    arguments. A row that is no static is an internal error."""
    row = _row(table, missing_code, name)
    if not row.static:
        raise_internal_error("CE0000", detail=f"the static '{name}' of {instance} is a method")
    return row.emit(codegen, None, instance, args)


def _row(table: Mapping[str, ContainerMethod], missing_code: str,
         name: str) -> ContainerMethod:
    row: Optional[ContainerMethod] = table.get(name)
    if row is None:
        raise_internal_error(missing_code, method=name)
    return row
