"""Platform-specific time declarations for Linux."""
from sushi_lang.sushi_stdlib.src._platform.posix.time import (
    declare_nanosleep,
    declare_clock_gettime,
)

# clockid values, verified with a probe (2026-08-29, glibc)
CLOCK_REALTIME = 0
CLOCK_MONOTONIC = 1

__all__ = [
    "declare_nanosleep",
    "declare_clock_gettime",
]
