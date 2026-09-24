"""Standard library function call emission package."""
from __future__ import annotations

from typing import Callable

from sushi_lang.backend.expressions.calls.stdlib.io import (
    emit_files_function,
)
from sushi_lang.backend.expressions.calls.stdlib.strings import emit_stdlib_string_call
from sushi_lang.backend.expressions.calls.stdlib.primitives import emit_stdlib_primitive_call
from sushi_lang.backend.expressions.calls.stdlib.math import emit_math_function
from sushi_lang.backend.expressions.calls.stdlib.time import emit_time_function
from sushi_lang.backend.expressions.calls.stdlib.env import emit_env_function
from sushi_lang.backend.expressions.calls.stdlib.random import emit_random_function
from sushi_lang.backend.expressions.calls.stdlib.process import emit_process_function
from sushi_lang.backend.expressions.calls.stdlib.net import emit_net_function

# One row per registry module, keyed by its `use` path. The semantic side answers for
# the same set (`semantics/stdlib_registry.py`); a module it answers and this table
# misses is a CE0055.
STDLIB_EMITTERS: dict[str, Callable] = {
    "time": emit_time_function,
    "sys/env": emit_env_function,
    "sys/process": emit_process_function,
    "math": emit_math_function,
    "random": emit_random_function,
    "io/files": emit_files_function,
    "net/socket": emit_net_function,
}

__all__ = [
    "STDLIB_EMITTERS",
    "emit_files_function",
    "emit_stdlib_string_call",
    "emit_stdlib_primitive_call",
    "emit_math_function",
    "emit_time_function",
    "emit_env_function",
    "emit_random_function",
    "emit_process_function",
    "emit_net_function",
]
