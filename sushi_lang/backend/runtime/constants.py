"""Constants for runtime operations."""
from __future__ import annotations

# errno constants (from errno.h)
# These values are standard across most UNIX-like systems
# Used for mapping system errors to Sushi FileError enum variants
ERRNO_EPERM = 1           # Operation not permitted
ERRNO_ENOENT = 2          # No such file or directory
ERRNO_EIO = 5             # I/O error
ERRNO_EACCES = 13         # Permission denied
ERRNO_EEXIST = 17         # File exists
ERRNO_ENOTDIR = 20        # Not a directory
ERRNO_EISDIR = 21         # Is a directory
ERRNO_EMFILE = 24         # Too many open files
ERRNO_ENOSPC = 28         # No space left on device


def errno_to_file_error_table(is_linux: bool) -> dict[int, int]:
    """errno value -> FileError variant tag.

    The tag order is CollectorPass._register_predefined_enums. ENAMETOOLONG and
    ELOOP do not agree between macOS (63, 62) and Linux (36, 40); the shared
    constants above do. An unmapped errno maps to ERRNO_DEFAULT_FILE_ERROR.
    """
    enametoolong = 36 if is_linux else 63
    eloop = 40 if is_linux else 62
    return {
        ERRNO_ENOENT: 0,     # NotFound
        ERRNO_EPERM: 1,      # PermissionDenied
        ERRNO_EACCES: 1,     # PermissionDenied
        ERRNO_EEXIST: 2,     # AlreadyExists
        ERRNO_EISDIR: 3,     # IsDirectory
        ERRNO_ENOSPC: 4,     # DiskFull
        ERRNO_EMFILE: 5,     # TooManyOpen
        enametoolong: 6,     # InvalidPath
        ERRNO_ENOTDIR: 6,    # InvalidPath
        eloop: 6,            # InvalidPath
        ERRNO_EIO: 7,        # IOError
    }


ERRNO_DEFAULT_FILE_ERROR = 8  # Other

FORMAT_STRINGS = {
    "i32": "%d",
    "i64": "%lld",
    "u32": "%u",
    "u64": "%llu",
    "str": "%s",
    "f32": "%g",
    "f64": "%g",
    "bool_true": "true",
    "bool_false": "false",
}
