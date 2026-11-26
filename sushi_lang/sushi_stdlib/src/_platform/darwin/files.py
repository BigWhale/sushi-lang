"""Platform-specific file system declarations for macOS."""
from llvmlite import ir
from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.sushi_stdlib.src._platform.posix.files import (
    declare_readdir as _declare_readdir_posix,
    declare_opendir,
    declare_closedir,
    declare_write,
    declare_close,
)

# errno values that differ from Linux
ENAMETOOLONG = 63
ELOOP = 62


# struct dirent: d_name offset, verified with an offsetof probe (2026-08-29).
# macOS: d_ino(8) d_seekoff(8) d_reclen(2) d_namlen(2) d_type(1), name at 21.
DIRENT_NAME_OFFSET = 21


def declare_readdir(module: ir.Module) -> ir.Function:
    """Declare readdir with the 64-bit-inode symbol for the arch."""
    name = "readdir$INODE64" if get_current_platform().arch == "x86_64" else "readdir"
    return _declare_readdir_posix(module, name)

__all__ = [
    "declare_opendir",
    "declare_readdir",
    "declare_closedir",
    "declare_write",
    "declare_close",
]
