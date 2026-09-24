"""Time module for Sushi standard library."""
from __future__ import annotations
import typing
from typing import Dict

from llvmlite import ir

from sushi_lang.semantics.typesys import BuiltinType, Type
from sushi_lang.sushi_stdlib.src.signatures import Signature, params_of


I32, I64 = BuiltinType.I32, BuiltinType.I64

# The ONE spelling of what each `<time>` function takes and answers (#550, #798).
TIME_SIGNATURES: Dict[str, Signature] = {
    "sleep":        Signature(params_of(I64), ok=I32, error="StdError"),
    "msleep":       Signature(params_of(I64), ok=I32, error="StdError"),
    "usleep":       Signature(params_of(I64), ok=I32, error="StdError"),
    "nanosleep":    Signature(params_of(I64, I64), ok=I32, error="StdError"),
    "now":          Signature(ok=I64, error="StdError"),
    "monotonic_ns": Signature(ok=I64, error="StdError"),
}


def is_builtin_time_function(name: str) -> bool:
    """Check if name is a built-in time module function."""
    return name in TIME_SIGNATURES


def get_builtin_time_function_return_type(name: str) -> Type:
    """The declared return type, from the row."""
    sig = TIME_SIGNATURES.get(name)
    if sig is None:
        raise ValueError(f"Unknown time function: {name}")
    return sig.return_type()


def validate_time_function_call(name: str, signature: typing.Any) -> None:
    """Validate a call to a built-in time function."""
    from sushi_lang.semantics.typesys import BuiltinType

    if name == 'nanosleep':
        if len(signature.params) != 2:
            raise TypeError(f"nanosleep expects 2 arguments, got {len(signature.params)}")

        param1_type = signature.params[0].type
        param2_type = signature.params[1].type

        if param1_type != BuiltinType('i64'):
            raise TypeError(f"nanosleep expects i64 for seconds, got {param1_type}")
        if param2_type != BuiltinType('i64'):
            raise TypeError(f"nanosleep expects i64 for nanoseconds, got {param2_type}")

    elif name in {'sleep', 'msleep', 'usleep'}:
        if len(signature.params) != 1:
            raise TypeError(f"{name} expects 1 argument, got {len(signature.params)}")

        param_type = signature.params[0].type
        if param_type != BuiltinType('i64'):
            raise TypeError(f"{name} expects i64, got {param_type}")

    elif name in {'now', 'monotonic_ns'}:
        if len(signature.params) != 0:
            raise TypeError(f"{name} expects no arguments, got {len(signature.params)}")


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for time functions."""
    from sushi_lang.sushi_stdlib.src.time import sleep
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module

    module = create_stdlib_module("time")

    sleep.generate_nanosleep(module)
    sleep.generate_sleep(module)
    sleep.generate_msleep(module)
    sleep.generate_usleep(module)

    from sushi_lang.sushi_stdlib.src.time import clock
    clock.generate_now(module)
    clock.generate_monotonic_ns(module)

    return module
