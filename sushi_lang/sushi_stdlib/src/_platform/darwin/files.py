"""Platform-specific file system declarations for macOS."""
from sushi_lang.sushi_stdlib.src._platform.posix.files import declare_write

# errno values that differ from Linux
ENAMETOOLONG = 63
ELOOP = 62

__all__ = ["declare_write"]
