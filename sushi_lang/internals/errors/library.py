"""Library system and .slib format errors (CE35xx)."""
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
    "library '{lib}' accepts compiler {requires}, this is {current}",
    Category.LIBRARY,
    "A source library is compiled by the CONSUMER's compiler, not the author's, so a library "
    "that built cleanly under one compiler can fail under a later one. That is the standard "
    "cost of source distribution and it is not fixable -- only declarable. Every .slib states "
    "`requires_compiler`; the default a build stamps is `~<major>.<minor>` of the building "
    "compiler, because pre-1.0 semver makes the minor the breaking unit. A warning was "
    "considered and rejected: a real incompatibility that is only warned about surfaces later "
    "as a confusing error inside library source the consumer never wrote. The escape, for an "
    "author testing a library forward against a new compiler, is --ignore-compiler-version. "
    "The check is skipped, never failed, when either version cannot be parsed."))


_add(ErrorMessage("CE3504", Severity.ERROR,
    "platform mismatch: library compiled for '{lib_platform}', current platform is '{current_platform}'",
    Category.LIBRARY, "Libraries must be compiled for the same platform they are used on."))


_add(ErrorMessage("CE3505", Severity.ERROR,
    "cannot determine the version of library '{lib}': {reason}",
    Category.LIBRARY,
    "A .slib records `library_version`, which it never used to: `library_name` came from the "
    "output filename and nothing stated a version at all. The value comes from `[package] "
    "version` in a nori.toml beside the sources when one exists, otherwise from an explicit "
    "--lib-version. Neither present is this error, and so is a --lib-version that CONTRADICTS "
    "the nori.toml -- silently preferring one would let a package ship under a version it does "
    "not claim."))

_add(ErrorMessage("CE3506", Severity.ERROR,
    "corrupted library file '{path}': source section truncated (expected {expected} bytes, got {actual})",
    Category.LIBRARY,
    "The container's sibling of CE3510/CE3511 for the source section that version 4 added "
    "between the metadata and the bitcode. Three codes rather than one because the registry "
    "text names which section is short, which is what tells a reader where the file was cut."))

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
