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

_add(ErrorMessage("RE2024", Severity.ERROR,
    "array element count %d is negative",
    Category.RUNTIME, "A count of ELEMENTS reached the fill with a negative value. The "
    "counted walk compares with an unsigned predicate, so -1 reads as four billion and the "
    "fill runs off the end of the buffer -- a memory-safety hole rather than a wrong answer, "
    "which is why it traps instead of being left to fall out. A clamp to zero would turn a "
    "wrong program into a silently empty array. It is worded for any element count and not "
    "for a repeat count alone, because every writer of N slots has the same hazard from the "
    "same cause; one code carries them, the precedent CE2017 sets for a count that goes "
    "wrong. Zero is not negative, so a run-time count of zero is DATA and gives an empty "
    "array. A range never reaches this: its count is an absolute value."))
