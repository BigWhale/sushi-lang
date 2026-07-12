"""Library system and .slib format errors (CE35xx).

This module owns its numeric range: a code may only be added in the file that
owns it, which is what makes the grouping structural rather than conventional.
"""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


#
# --- Runtime Error Codes (RExxxx) ---
#
# Runtime errors occur during program execution (not during compilation).
# These are emitted as runtime checks in the generated LLVM code.
# Convention: RE prefix indicates Runtime Error
#
# Library System Errors (CE35xx)
_add(ErrorMessage("CE3500", Severity.ERROR,
    "library output path must have .slib extension: '{path}'",
    Category.LIBRARY, "Library compilation requires output file with .slib extension."))

_add(ErrorMessage("CE3501", Severity.ERROR,
    "main() function not allowed in library mode",
    Category.LIBRARY, "Libraries cannot have a main() function. Remove it or compile as executable."))

_add(ErrorMessage("CE3502", Severity.ERROR,
    "library not found: '{lib}' (searched: {paths})",
    Category.LIBRARY, "Library bitcode and manifest files not found in search paths."))

_add(ErrorMessage("CE3503", Severity.ERROR,
    "invalid library manifest '{path}': {reason}",
    Category.LIBRARY, "Library manifest file is malformed or missing required fields."))

_add(ErrorMessage("CE3504", Severity.ERROR,
    "platform mismatch: library compiled for '{lib_platform}', current platform is '{current_platform}'",
    Category.LIBRARY, "Libraries must be compiled for the same platform they are used on."))

_add(ErrorMessage("CE3506", Severity.ERROR,
    "cannot use --lib with --link: libraries cannot link other libraries yet",
    Category.LIBRARY, "Transitive library dependencies are not yet supported."))

_add(ErrorMessage("CE3507", Severity.ERROR,
    "failed to link library '{lib}': {reason}",
    Category.LIBRARY, "LLVM bitcode linking failed for the specified library."))

# Binary library format errors (.slib)
_add(ErrorMessage("CE3508", Severity.ERROR,
    "invalid library file '{path}': not a valid .slib file (bad magic)",
    Category.LIBRARY, "File does not start with SUSHILIB magic bytes."))

_add(ErrorMessage("CE3509", Severity.ERROR,
    "unsupported library format version '{version}' in '{path}' (compiler supports version {supported})",
    Category.LIBRARY, "Library was created with incompatible format version."))

_add(ErrorMessage("CE3510", Severity.ERROR,
    "corrupted library file '{path}': metadata section truncated (expected {expected} bytes, got {actual})",
    Category.LIBRARY, "Library file is incomplete or corrupted."))

_add(ErrorMessage("CE3511", Severity.ERROR,
    "corrupted library file '{path}': bitcode section truncated (expected {expected} bytes, got {actual})",
    Category.LIBRARY, "Library file is incomplete or corrupted."))

_add(ErrorMessage("CE3512", Severity.ERROR,
    "invalid library metadata in '{path}': {reason}",
    Category.LIBRARY, "MessagePack decoding failed or metadata schema is invalid."))

_add(ErrorMessage("CE3513", Severity.ERROR,
    "library file too large '{path}': {size} bytes exceeds maximum {max_size} bytes",
    Category.LIBRARY, "Library file exceeds reasonable size limit."))
