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

# Documentation blocks (CW7001-CW7006, CE7001-CE7008)
_add(ErrorMessage("CW7001", Severity.WARNING,
    "this documentation block documents nothing", Category.DOCS,
    "A block attaches to the declaration on the NEXT line. A blank line breaks the "
    "attachment, and so does an ordinary `#` comment: both are absorbed into the "
    "newline token, so the compiler cannot tell one from the other. The escape is to "
    "move the comment, or to move the block. A block that is the first item in its "
    "file documents the unit and never warns."))

# CW7002 to CW7006 are the completeness lints, and every one of them is behind
# `--warn-missing-docs`. What a block CLAIMS is checked always, because a claim that
# contradicts the declaration is wrong whatever the project's policy is. What a block
# OMITS is policy, so it waits for the flag. documentation.md section 6 is the contract.
#
# The three lints about a block's contents presuppose a block (R33). A declaration with
# no block is CW7002 and nothing else, so one omission stays one diagnostic.

_add(ErrorMessage("CW7002", Severity.WARNING,
    "this {kind} has no documentation block: '{name}'",
    Category.DOCS, "Every declaration is asked the same question, public and private. "
                   "The `public` marker is not the test: an internal API is documented "
                   "surface as much as an exported one, and a reader of the code is a "
                   "reader. Two declarations are exempt. `fn main()` is nobody's API, "
                   "and a library cannot declare one at all. An `unsafe external` block "
                   "and the declarations in it carry `because \"...\"`, which "
                   "acknowledges the contract that matters at that seam."))

_add(ErrorMessage("CW7003", Severity.WARNING,
    "the parameter '{name}' of '{callable}' is not documented",
    Category.DOCS, "A block that documents some of the parameters and not the rest is "
                   "the shape a reader trusts least: it looks complete. `self` is never "
                   "asked for, because the builders lift the receiver onto the "
                   "declaration and it is not a parameter by the time the pass reads "
                   "one. A declaration with NO block is CW7002 instead."))

_add(ErrorMessage("CW7004", Severity.WARNING,
    "'{name}' returns a value, and no '- Returns:' tag says what it is",
    Category.DOCS, "The tag describes T, not the Result that wraps it "
                   "(documentation.md section 3). A callable that returns `~` returns "
                   "nothing to describe and is never asked. A declaration with NO block "
                   "is CW7002 instead."))

_add(ErrorMessage("CW7005", Severity.WARNING,
    "'{name}' declares an error arm, and no '- Errors:' tag says when it fails",
    Category.DOCS, "A function written `fn f() T | E` names its own error type, so the "
                   "author chose to have more than one way to fail and the reader needs "
                   "to know which. A function on the implicit StdError arm is not asked. "
                   "A declaration with NO block is CW7002 instead."))

_add(ErrorMessage("CW7006", Severity.WARNING,
    "this unit has no documentation block",
    Category.DOCS, "A unit block is the first block in a file, and it travels in a "
                   "`.slib` as `unit_docs`, which `--lib-info` prints under the unit "
                   "name. A library whose units say nothing is the first hole a reader "
                   "meets. This is the one lint about something that is not there, so "
                   "it carries no caret."))

# FFI / Foreign Function Interface (CW5001, CE5001-CE5008)
_add(ErrorMessage("CW5001", Severity.WARNING,
    "unsafe external block suspends four Sushi guarantees (add `because \"...\"` to acknowledge)",
    Category.TYPE, "An `unsafe external` block disables borrow checking, RAII, Result/Maybe error handling, and bounds/null safety for the foreign declarations it contains. Provide a `because \"<reason>\"` clause to acknowledge the contract and silence this warning."))
