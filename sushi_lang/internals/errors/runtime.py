"""Runtime errors (RExxxx) -- trapped at run time, not compile time.

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


# Array bounds errors
_add(ErrorMessage("RE2020", Severity.ERROR,
    "array index out of bounds",
    Category.RUNTIME, "Array access with index outside valid range [0, size)."))

# Memory allocation errors
_add(ErrorMessage("RE2021", Severity.ERROR,
    "memory allocation failed",
    Category.RUNTIME, "System could not allocate memory (malloc/realloc returned NULL)."))

# HashMap probe exhaustion
_add(ErrorMessage("RE2022", Severity.ERROR,
    "insert into an unusable HashMap",
    Category.RUNTIME, "HashMap.insert() probed every bucket without finding a slot. A live map "
    "always resizes below a 0.75 load factor, so this means the map has no buckets at all -- it "
    "was destroyed. Using a destroyed map is CE2406, but the borrow checker only sees a literal "
    "`m.destroy()`, so a destroy through a `&poke` parameter reaches here instead."))

# Pattern match exhaustion
_add(ErrorMessage("RE2023", Severity.ERROR,
    "no match arm matched the value",
    Category.RUNTIME, "A nested pattern reached the end of its arms without matching. "
    "Exhaustiveness checking should make this unreachable."))
