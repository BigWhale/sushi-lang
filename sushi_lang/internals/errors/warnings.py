"""Warnings (CWxxxx).

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


_add(ErrorMessage("CW2001", Severity.WARNING,
    "unused Result<T> value (use .realise() or if statement to handle the result)",
    Category.TYPE, "Result<T> values should be explicitly handled to avoid losing error information."))

_add(ErrorMessage("CW2511", Severity.WARNING,
    "?? operator used in main function (consider explicit error handling for clarity)",
    Category.TYPE, "While ?? works in main, explicit error handling with .realise(), if statements, or match expressions makes error behavior clearer at the program entry point."))

_add(ErrorMessage("CW2409", Severity.WARNING,
    "re-borrowing '{name}' as &poke (nested mutable borrow)",
    Category.TYPE, "Creating a &poke borrow of a &poke reference parameter passes through exclusive access. Ensure the original reference is not used until the nested borrow ends."))

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

_add(ErrorMessage("CW1003", Severity.WARNING,
    "variable '{name}' is only used through borrows (not directly accessed)", Category.SCOPE,
    "A variable was declared but only accessed through &references. This is valid but may indicate unnecessary indirection."))

# Unit/module warnings
_add(ErrorMessage("CW3001", Severity.WARNING,
    "duplicate use statement for unit '{unit}'", Category.UNIT,
    "A unit was already imported earlier in this file. The duplicate use statement has no effect."))

_add(ErrorMessage("CW3505", Severity.WARNING,
    "platform mismatch: library compiled for '{lib_platform}', current platform is '{current_platform}'",
    Category.UNIT, "Library was compiled for a different platform. This may cause runtime issues."))

# FFI / Foreign Function Interface (CW5001, CE5001-CE5008)
_add(ErrorMessage("CW5001", Severity.WARNING,
    "unsafe external block suspends four Sushi guarantees (add `because \"...\"` to acknowledge)",
    Category.TYPE, "An `unsafe external` block disables borrow checking, RAII, Result/Maybe error handling, and bounds/null safety for the foreign declarations it contains. Provide a `because \"<reason>\"` clause to acknowledge the contract and silence this warning."))
