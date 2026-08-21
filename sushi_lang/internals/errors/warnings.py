"""Warnings (CWxxxx)."""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


_add(ErrorMessage("CW2001", Severity.WARNING,
    "unused Result@(T) value (use .realise() or if statement to handle the result)",
    Category.TYPE, "Result@(T) values should be explicitly handled to avoid losing error information."))

_add(ErrorMessage("CW2511", Severity.WARNING,
    "?? operator used in main function (consider explicit error handling for clarity)",
    Category.TYPE, "While ?? works in main, explicit error handling with .realise(), if statements, or match expressions makes error behavior clearer at the program entry point."))

# CW2409 (re-borrowing as poke, WARNING) was deleted: its only trigger was forwarding a
# whole poke parameter to a poke argument -- the composition idiom the borrow model
# mandates. The call-site borrow ends with the statement; a same-statement conflict is
# CE2403/CE2407, and the callee cannot store or outlive the reference (the CE2411
# family). Field forwarding (poke cfg.port) was always silent, so the warning also made
# the whole stricter than the part. Retired 2026-08-21 with the first stdlib consumer
# (encoding/msgpack), whose cursor threading fired it 40 times per importing program.

# General warnings
_add(ErrorMessage("CW0001", Severity.WARNING,
    "missing trailing newline", Category.GENERAL,
    "Source file should end with a newline character."))

# Rebinding / scope warnings
_add(ErrorMessage("CW1001", Severity.WARNING,
    "unused variable '{name}'", Category.SCOPE,
    "A variable was declared with 'let' but never used."))

_add(ErrorMessage("CW1002", Severity.WARNING,
    "declared variable '{name}' already exists in an outer scope", Category.SCOPE,
    "A variable was declared with 'let' outside of this scope."))

# Unit/module warnings
_add(ErrorMessage("CW3001", Severity.WARNING,
    "duplicate use statement for unit '{unit}'", Category.UNIT,
    "A unit was already imported earlier in this file. The duplicate use statement has no effect."))

# CW3505 (platform mismatch, WARNING) was deleted: it was a byte-for-byte duplicate of
# CE3504 at warning severity, and a platform mismatch is not a warning -- the link
# cannot succeed. The error, CE3504, is the survivor.

_add(ErrorMessage("CW3506", Severity.WARNING,
    "library perk implementation for '{type}' could not be loaded and was skipped",
    Category.LIBRARY, "A perk implementation shipped by a library failed to deserialize. "
                      "Methods it provides will be unavailable unless the consumer supplies its own."))

# FFI / Foreign Function Interface (CW5001, CE5001-CE5008)
_add(ErrorMessage("CW5001", Severity.WARNING,
    "unsafe external block suspends four Sushi guarantees (add `because \"...\"` to acknowledge)",
    Category.TYPE, "An `unsafe external` block disables borrow checking, RAII, Result/Maybe error handling, and bounds/null safety for the foreign declarations it contains. Provide a `because \"<reason>\"` clause to acknowledge the contract and silence this warning."))
