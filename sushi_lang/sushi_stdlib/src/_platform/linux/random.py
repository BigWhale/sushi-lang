"""Platform-specific random declarations for Linux."""
from sushi_lang.sushi_stdlib.src._platform.posix.random import (
    declare_random,
    declare_srandom,
)

__all__ = [
    "declare_random",
    "declare_srandom",
]
