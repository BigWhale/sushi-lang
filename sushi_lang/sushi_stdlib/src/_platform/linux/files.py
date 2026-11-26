"""Platform-specific file system declarations for Linux."""
from sushi_lang.sushi_stdlib.src._platform.posix.files import (
    declare_opendir,
    declare_readdir,
    declare_closedir,
    declare_write,
    declare_close,
)

# errno values that differ from macOS
ENAMETOOLONG = 36
ELOOP = 40

# struct dirent: d_name offset, verified with an offsetof probe (2026-08-29, glibc).
# Linux: d_ino(8) d_off(8) d_reclen(2) d_type(1), name at 19; both arches agree.
DIRENT_NAME_OFFSET = 19

__all__ = [
    "declare_opendir",
    "declare_readdir",
    "declare_closedir",
    "declare_write",
    "declare_close",
]
