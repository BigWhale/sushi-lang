"""
Standard library function call emission package.

This package handles external calls to precompiled stdlib functions for
I/O operations, string methods, primitive conversions, math, time, env,
random, and process functions. It re-exports every public emit_* symbol
from its submodules so the original flat import surface is preserved.
"""
from __future__ import annotations

from sushi_lang.backend.expressions.calls.stdlib.io import (
    emit_stdlib_stdio_call,
    emit_stdlib_file_call,
    emit_files_function,
)
from sushi_lang.backend.expressions.calls.stdlib.strings import emit_stdlib_string_call
from sushi_lang.backend.expressions.calls.stdlib.primitives import emit_stdlib_primitive_call
from sushi_lang.backend.expressions.calls.stdlib.math import emit_math_function
from sushi_lang.backend.expressions.calls.stdlib.time import emit_time_function
from sushi_lang.backend.expressions.calls.stdlib.env import emit_env_function
from sushi_lang.backend.expressions.calls.stdlib.random import emit_random_function
from sushi_lang.backend.expressions.calls.stdlib.process import emit_process_function

__all__ = [
    "emit_stdlib_stdio_call",
    "emit_stdlib_file_call",
    "emit_files_function",
    "emit_stdlib_string_call",
    "emit_stdlib_primitive_call",
    "emit_math_function",
    "emit_time_function",
    "emit_env_function",
    "emit_random_function",
    "emit_process_function",
]
