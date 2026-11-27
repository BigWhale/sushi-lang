"""Platform-specific file system declarations for Linux."""
from sushi_lang.sushi_stdlib.src._platform.posix.files import declare_write

# errno values that differ from macOS
ENAMETOOLONG = 36
ELOOP = 40

__all__ = ["declare_write"]
