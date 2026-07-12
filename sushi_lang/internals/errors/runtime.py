"""Runtime errors (RExxxx) -- trapped at run time, not compile time.

This module owns its numeric range: a code may only be added in the file that
owns it, which is what makes the grouping structural rather than conventional.

A runtime diagnostic is a C string baked into the binary, so `text` here IS what
the program prints -- the emitters take it from this registry rather than carrying
hand-written strings of their own. Two consequences:

  - For `emit_runtime_error_with_values`, the text IS the printf format string:
    its `%d` / `%s` conversions must match the values the call site passes.
  - For `emit_runtime_error`, the text may carry `{}` placeholders, formatted at
    codegen time.

A text should therefore use `%` conversions or `{}` placeholders, never both.
"""
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
    "`&poke` parameter (#168). This trap remains as defense-in-depth for the destroy-effect "
    "summary's deliberate under-approximation: a generic callee, an extension method destroying "
    "its implicit `self`, a library callee, or an argument that is not a bare name."))

# Pattern match exhaustion
_add(ErrorMessage("RE2023", Severity.ERROR,
    "no match arm matched the value (expected {pattern})",
    Category.RUNTIME, "A nested pattern reached the end of its arms without matching. "
    "Exhaustiveness checking should make this unreachable."))
