"""Validation and pure helpers for the built-in methods on a function value.

The ir-free half, matching `own.py` and `list.py`: method recognition and Pass-2 argument
validation. LLVM emission stays in the backend, and there is nothing new to emit --
`backend/expressions/memory.py::emit_value_clone` already routes a `FunctionType` to
`_clone_function_value`, which duplicates the heap environment through the fat pointer's
`clone_ptr` slot.

**Why a function value needs a clone at all.** A closure read out of a struct field or out
of a container is a BORROW (closures T1.5): the struct and the container keep the
environment and still free it. Consuming that borrow is CE2411, whose help text says
"clone it to take an independent value" -- so the method has to exist, or the compiler is
naming a fix the language does not have. It did exactly that until this module: dispatch
fell through to the user extension-method path, which mangled the name to
`fn(i32) - i32_clone` and raised a bare `KeyError`. That is MM.md finding A2's failure mode,
one family further on.

`clone` is currently the only entry. Bound-method values and a C-callback accessor are
Tier 2 items and would live here.
"""
from __future__ import annotations

from typing import Any

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import FunctionType, Type
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.type_display import display_type


def is_builtin_function_method(method_name: str) -> bool:
    """Is `method_name` a built-in method on a function value?"""
    return method_name == "clone"


def function_method_return_type(method_name: str, fn_type: FunctionType) -> Type | None:
    """Return type of a built-in function-value method, or None if there is no such pair.

    `clone` returns the receiver's own type. Function types are invariant and
    capture-agnostic, so the clone of a `fn(i32) -> i32` is a `fn(i32) -> i32` whether or
    not it owns an environment.
    """
    if method_name == "clone":
        return fn_type
    return None


def validate_function_method_with_validator(
    call: MethodCall,
    fn_type: FunctionType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate a built-in function-value method call."""
    if call.method == "clone":
        _validate_function_clone(call, fn_type, reporter)
    else:
        # Unreachable if is_builtin_function_method was consulted first.
        raise_internal_error("CE0080", method=call.method)


def _validate_function_clone(call: MethodCall, fn_type: FunctionType, reporter: Any) -> None:
    """clone() takes no arguments, like every other clone in the language."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(fn_type)}.clone", expected=0, got=len(call.args))
