"""
Environment variable module for Sushi standard library.

Provides functions to read and modify environment variables.

Available Functions:
    getenv(string key) -> Maybe<string>
        Get an environment variable by key.
        Returns Maybe.Some(value) if found, Maybe.None() otherwise.

    setenv(string key, string value) -> Result<i32>
        Set an environment variable.
        Returns Result.Ok(0) on success, Result.Err() on failure.

Example Usage:
    use <sys/env>

    fn main() i32:
        # Get an environment variable
        let Maybe<string> home = getenv("HOME")
        match home:
            Maybe.Some(path) -> println("HOME is {path}")
            Maybe.None() -> println("HOME not set")

        # Set an environment variable
        let i32 result = setenv("MY_VAR", "hello")??
        println("Variable set successfully")

        return Result.Ok(0)

Implementation Notes:
    - getenv() returns Maybe<string> (no error message needed for missing vars)
    - setenv() returns Result<i32> (can fail due to ENOMEM or invalid name)
    - Uses POSIX getenv() and setenv() from platform module
    - Environment changes affect current process and child processes only
    - Returned strings from getenv() are copied to Sushi strings (safe)
"""
from __future__ import annotations
import typing
from llvmlite import ir

if typing.TYPE_CHECKING:
    from semantics.typesys import Type
    #from semantics.symbols import Signature

from stdlib.src import type_converters


def is_builtin_env_function(name: str) -> bool:
    """Check if name is a built-in env module function."""
    return name in {
        'getenv',
        'setenv',
    }


def get_builtin_env_function_return_type(name: str) -> Type:
    """Get the return type for a built-in env function."""
    from semantics.typesys import BuiltinType, ResultType, BuiltinType
    from backend.generics.maybe import ensure_maybe_type_in_table
    #from semantics.symbols import get_enum_table

    if name == 'getenv':
        # getenv(string key) -> Maybe<string>
        enum_table = get_enum_table()
        maybe_string_type = ensure_maybe_type_in_table(enum_table, BuiltinType.STRING)
        return maybe_string_type

    elif name == 'setenv':
        # setenv(string key, string value) -> Result<i32>
        inner = BuiltinType('i32')
        return ResultType(inner)

    raise ValueError(f"Unknown env function: {name}")


def validate_env_function_call(name: str, signature: Signature) -> None:
    """Validate a call to a built-in env function."""
    from semantics.typesys import BuiltinType

    if name == 'getenv':
        # getenv(string key) -> Maybe<string>
        if len(signature.params) != 1:
            raise TypeError(f"getenv expects 1 argument, got {len(signature.params)}")

        param_type = signature.params[0].type
        if param_type != BuiltinType.STRING:
            raise TypeError(f"getenv expects string, got {param_type}")

    elif name == 'setenv':
        # setenv(string key, string value) -> Result<i32>
        if len(signature.params) != 2:
            raise TypeError(f"setenv expects 2 arguments, got {len(signature.params)}")

        key_type = signature.params[0].type
        value_type = signature.params[1].type

        if key_type != BuiltinType.STRING:
            raise TypeError(f"setenv expects string for key, got {key_type}")
        if value_type != BuiltinType.STRING:
            raise TypeError(f"setenv expects string for value, got {value_type}")


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for env functions."""
    from stdlib.src.sys.env import functions
    from stdlib.src.ir_common import create_stdlib_module

    module = create_stdlib_module("sys.env")

    # Generate all env functions
    functions.generate_getenv(module)
    functions.generate_setenv(module)

    return module
