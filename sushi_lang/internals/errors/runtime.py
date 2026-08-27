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
