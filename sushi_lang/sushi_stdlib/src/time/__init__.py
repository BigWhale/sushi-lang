"""Time module for Sushi standard library."""
from __future__ import annotations
import typing
from llvmlite import ir

if typing.TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type


def is_builtin_time_function(name: str) -> bool:
    """Check if name is a built-in time module function."""
    return name in {
        'nanosleep',
        'sleep',
        'msleep',
        'usleep',
    }


def get_builtin_time_function_return_type(name: str) -> Type:
    """Get the return type for a built-in time function."""
    from sushi_lang.semantics.typesys import BuiltinType

    if name in {'nanosleep', 'sleep', 'msleep', 'usleep'}:
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        return GenericTypeRef("Result", (BuiltinType('i32'), UnknownType("StdError")))

    raise ValueError(f"Unknown time function: {name}")


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


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for time functions."""
    from sushi_lang.sushi_stdlib.src.time import sleep
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module

    module = create_stdlib_module("time")

    sleep.generate_nanosleep(module)
    sleep.generate_sleep(module)
    sleep.generate_msleep(module)
    sleep.generate_usleep(module)

    return module
