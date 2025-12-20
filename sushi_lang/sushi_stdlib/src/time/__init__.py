"""
Time module for Sushi standard library.

Provides time-related functions for delays, timestamps, and timing operations.

Available Functions:
    nanosleep(i64 seconds, i64 nanoseconds) -> Result<i32>
        Sleep for specified duration with nanosecond precision.
        Returns 0 on success, remaining microseconds if interrupted.

    sleep(i64 seconds) -> Result<i32>
        Sleep for specified number of seconds.
        Convenience wrapper around nanosleep.

    msleep(i64 milliseconds) -> Result<i32>
        Sleep for specified number of milliseconds.
        Convenience wrapper around nanosleep.

    usleep(i64 microseconds) -> Result<i32>
        Sleep for specified number of microseconds.
        Convenience wrapper around nanosleep.

Example Usage:
    use <time>

    fn main() i32:
        # Sleep for 2 seconds
        let i32 result = sleep(2)??

        # Sleep for 500 milliseconds
        let i32 result = msleep(500)??

        # Sleep for 1.5 seconds precisely
        let i32 result = nanosleep(1, 500000000)??

        return Result.Ok(0)

Implementation Notes:
    - All functions return Result<i32> for error propagation
    - Return value is 0 on success, remaining microseconds if interrupted
    - Uses POSIX nanosleep() from platform module
    - Handles EINTR (signal interruption) correctly
    - Precision limited by OS scheduler (typically ~1ms minimum)
"""
from __future__ import annotations
import typing
from llvmlite import ir

if typing.TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    #from sushi_lang.semantics.symbols import Signature

from sushi_lang.sushi_stdlib.src import type_converters


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
    from sushi_lang.semantics.typesys import BuiltinType, ResultType

    if name in {'nanosleep', 'sleep', 'msleep', 'usleep'}:
        # All sleep functions return Result<i32, StdError>
        inner = BuiltinType('i32')
        from sushi_lang.semantics.typesys import UnknownType
        return ResultType(ok_type=inner, err_type=UnknownType("StdError"))

    raise ValueError(f"Unknown time function: {name}")


def validate_time_function_call(name: str, signature: Signature) -> None:
    """Validate a call to a built-in time function."""
    from sushi_lang.semantics.typesys import BuiltinType

    if name == 'nanosleep':
        # nanosleep(i64 seconds, i64 nanoseconds) -> Result<i32>
        if len(signature.params) != 2:
            raise TypeError(f"nanosleep expects 2 arguments, got {len(signature.params)}")

        param1_type = signature.params[0].type
        param2_type = signature.params[1].type

        if param1_type != BuiltinType('i64'):
            raise TypeError(f"nanosleep expects i64 for seconds, got {param1_type}")
        if param2_type != BuiltinType('i64'):
            raise TypeError(f"nanosleep expects i64 for nanoseconds, got {param2_type}")

    elif name in {'sleep', 'msleep', 'usleep'}:
        # sleep/msleep/usleep(i64 duration) -> Result<i32>
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

    # Generate all time functions
    sleep.generate_nanosleep(module)
    sleep.generate_sleep(module)
    sleep.generate_msleep(module)
    sleep.generate_usleep(module)

    return module
