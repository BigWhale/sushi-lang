"""Runtime errors (RExxxx) -- trapped at run time, not compile time."""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


# Array bounds errors. The text is the printf format: two %d, matching the
# (index, size) pair the bounds check passes.
_add(ErrorMessage("RE2020", Severity.ERROR,
    "array index %d out of bounds for array of size %d",
    Category.RUNTIME, "Array access with index outside valid range [0, size)."))

# Memory allocation errors
_add(ErrorMessage("RE2021", Severity.ERROR,
    "memory allocation failed",
    Category.RUNTIME, "System could not allocate memory (malloc/realloc returned NULL)."))

# HashMap probe exhaustion
_add(ErrorMessage("RE2022", Severity.ERROR,
    "insert into an unusable HashMap: no free bucket",
    Category.RUNTIME, "HashMap.insert() probed every bucket without finding a slot. A live map "
    "always resizes below a 0.75 load factor, so this means the map has no buckets at all -- it "
    "was destroyed. Using a destroyed map is CE2406, which now also catches a destroy through a "
    "`poke` parameter (#168). This trap remains as defense-in-depth for the destroy-effect "
    "summary's deliberate under-approximation: a generic callee, an extension method destroying "
    "its implicit `self`, a library callee, or an argument that is not a bare name."))

# Pattern match exhaustion
_add(ErrorMessage("RE2023", Severity.ERROR,
    "no match arm matched the value (expected {pattern})",
    Category.RUNTIME, "A nested pattern reached the end of its arms without matching. "
    "Exhaustiveness checking should make this unreachable."))

# RE2024 ("array element count %d is negative") was RETIRED before it ever shipped. It
# trapped a negative count reaching a fill or a copy, because the counted walk compares with
# an unsigned predicate and -1 would read as four billion. Both writers now CLAMP instead --
# `clamp_range` for a copy and `_clamp_count` for a repeated element -- which removes the
# hazard by construction rather than by a guard that has to fire, and matches `string.s` and
# `string.ss`, which have always clamped. A count of zero was already data rather than an
# error, so a negative one reaching the same answer needed no rule of its own.

# A false assert
_add(ErrorMessage("RE2026", Severity.ERROR,
    "assertion failed at {where}",
    Category.RUNTIME, "An `assert(cond)` or `assert(cond, message)` found its condition false. "
    "`{where}` is the file, the line and the column of the `assert`, with the file named as a "
    "compile-time diagnostic names it; a message, when there is one, follows after `: `. An "
    "assert states an invariant, so a failure is a defect and not data: the program stops "
    "with exit code 1, and no error channel catches it. A failure that a caller can handle "
    "belongs in the channel, `| E` (docs/design/assert.md)."))

# A foreign return declared non-null (#1085)
_add(ErrorMessage("RE2025", Severity.ERROR,
    "a foreign call returned a null pointer where its declaration says non-null",
    Category.RUNTIME, "An `unsafe external` function declared to return a plain `string` or "
    "`ptr` answered NULL. Null is never a Sushi value, so a plain pointer type asserts that C "
    "cannot answer one, and the call stops the program instead of passing the null on to "
    "`strlen` or to the next C call. Declare the return `Maybe@(string)` or `Maybe@(ptr)` when "
    "C can answer NULL: the call then answers `Maybe.None` (docs/ffi.md, \"Null at the "
    "boundary\")."))
