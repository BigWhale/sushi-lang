"""Library system and .slib format errors (CE35xx)."""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


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
    "version` in the nori.toml in the current directory when one exists, otherwise from an "
    "explicit --lib-version. A nori.toml in a parent directory or beside the sources is not "
    "read (#1066). Neither present is this error, and so is a --lib-version that CONTRADICTS "
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

_add(ErrorMessage("CE3515", Severity.ERROR,
    "cannot read library file '{path}': {reason}",
    Category.LIBRARY,
    "The operating system refused to open or to read the file: a directory, a file with no "
    "read permission, or an I/O failure. The reason names the cause in the words the "
    "slib-info tool uses for the same errno, so both halves of --lib-info say the same thing. "
    "It used to be a Python traceback from the --lib-info fallback and CE0000 from a "
    "`use <lib/...>` that named a directory (#943)."))

_add(ErrorMessage("CE3516", Severity.ERROR,
    "'{path}' is not a library file: the name of a .slib file ends in .slib",
    Category.LIBRARY,
    "`--lib-info` reads a library, and a library file is named `<name>.slib`. Both halves "
    "of the command refuse another name before they open the file, with this code, so a "
    "file that happens to hold a library under another name is refused as well. It used "
    "to be an uncoded line on stderr from each half, worded differently (#977)."))

_add(ErrorMessage("CE3517", Severity.ERROR,
    "cannot build library '{lib}': {reason} [{nori_code}]",
    Category.LIBRARY,
    "A --lib build reads the nori.toml in the current directory for the library version. "
    "A nori.toml that exists must be valid: every fault that nori's manifest reader refuses "
    "stops the build here, a file that is not TOML or UTF-8, a field of the wrong TOML type, "
    "and also a field with a bad value (a missing field, a bad package name, a bad version, "
    "a bad dependency), although the compiler reads the version alone. The user wrote a "
    "manifest and it is wrong, and a build that used --lib-version would hide the fault and "
    "the version conflict of CE3505. The reason is the text of the nori code in brackets -- "
    "the one manifest reader makes the check for the compiler and for nori, so both name "
    "the file and the field in the same words. It used to be a silent skip, and before that "
    "CE0000 (#1040); a value fault was a silent skip until #1066. A missing nori.toml is "
    "not this error, and a nori.toml that cannot be read is CE3518."))

_add(ErrorMessage("CE3518", Severity.ERROR,
    "cannot build library '{lib}': cannot read '{path}': {reason}",
    Category.LIBRARY,
    "A --lib build reads the nori.toml in the current directory for the library version. "
    "A nori.toml that exists but cannot be opened or read (no read permission, a directory, "
    "a broken link) stops the build here, and the reason is the one that the operating "
    "system gives. It is not a missing manifest: a build that used --lib-version would hide "
    "a manifest that the user wrote. A nori.toml that can be read and is not valid is "
    "CE3517 (#1066)."))

# CE3514 ("a {kind} library cannot carry a `public use`") was RETIRED when #585 landed
# the manifest record. It refused the statement at build time for as long as a compiled
# library had nowhere to put it: `public use X` makes X's public names the unit's own,
# and a consumer reading records rather than text would have seen the unit's own names
# and missed the ones it handed on, silently. The manifest carries a `reexports` list
# now -- one record per statement, keyed by the unit that wrote it -- and the consumer
# composes the namespace from it, so a compiled library re-exports exactly as a source
# one does. `docs/design/unit-namespaces.md` section 8.1, rule 3.
