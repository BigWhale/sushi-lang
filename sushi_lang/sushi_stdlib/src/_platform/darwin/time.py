"""Platform-specific time declarations for macOS."""
from sushi_lang.sushi_stdlib.src._platform.posix.time import (
    declare_nanosleep,
)

# Re-exported for the platform dispatcher (get_platform_module); referenced dynamically.
__all__ = [
    "declare_nanosleep",
]
