"""Platform-specific environment variable declarations for macOS."""
from sushi_lang.sushi_stdlib.src._platform.posix.env import (
    declare_getenv,
    declare_setenv,
)

# Re-exported for the platform dispatcher (get_platform_module); referenced dynamically.
__all__ = [
    "declare_getenv",
    "declare_setenv",
]
