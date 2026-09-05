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

_add(ErrorMessage("CW3002", Severity.WARNING,
    "'{name}' shadows the {kind} '{owner}' exports", Category.UNIT,
    "A program's own declaration takes priority over a name a source library or a bundled stdlib module exports, and that is legal: a private function is emitted with internal linkage, so the two are separate symbols. The consumer's call binds to the consumer's declaration, the library's own body keeps calling its own, and both declarations being public is no longer a clash at all -- CE3003 retired, and an unqualified name with two candidates is CE3012 at the use. It warns because shadowing an export is rarely intended, and because the reader of the call site cannot see which of the two answers it. Rename your declaration, or keep it and accept that the library's body is unaffected. A name the library declares privately is shadowed the same way and says nothing, because each declaration carries the unit that declared it; a library TYPE is still one name for the program (CE3011). Write `use <lib/name> as alias` to put the export behind a dot and take the shadow away."))

_add(ErrorMessage("CW3003", Severity.WARNING,
    "this library extends '{type}', a type it does not declare", Category.UNIT,
    "The warning fires at `--lib` build time and nowhere else: shipping is when the "
    "claim becomes other people's problem, and it is the moment the author is present. "
    "A method is found on the receiver's type, so an extension puts its method name on "
    "the type for every consumer of the library, and a second library that claims the "
    "same name on the same type makes the two unusable together -- CE0101, at a "
    "consumer who can edit neither. A builtin target is not exempt: `i32` is the most "
    "collidable target of all, because every unit of every program can reach it. A perk "
    "implementation does not warn, because the consumer's own implementation is the "
    "sanctioned override, so that claim has an escape. An extension inside an ordinary "
    "program stays silent, `extend i32 squared()` is idiomatic Sushi there. To ship the "
    "method without the claim, declare your own wrapper type and extend that; to accept "
    "the claim, publish it -- `--lib-info` lists it under 'Foreign Extensions'."))

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

_add(ErrorMessage("CW3004", Severity.WARNING,
    "'{alias}' binds an empty namespace", Category.UNIT,
    "The import brought no name that a qualified form could reach, so the `as` clause does nothing. It is a warning and not an error because a namespace is empty for three reasons and only one of them is a mistake: a method interface such as `<io/stdio>` can never bring a name; a unit that is nothing but `extend` blocks exports methods rather than names, and is load-bearing anyway; and a public surface that happens to be empty today is one declaration away from changing. Refusing the first two would refuse a good import for a redundant clause, and refusing the third would make an error appear and disappear as a library grew. The import still did its work. Drop the `as`."))

_add(ErrorMessage("CW3005", Severity.WARNING,
    "`public use` of '{origin}' re-exports nothing", Category.UNIT,
    "The import brought no public name to hand on, so the `public` marker does nothing: the unit's importers get exactly what they would get without it. It is a warning and not an error for the reasons CW3004 gives an empty alias: a method interface such as `<collections/strings>` brings no name and never will, a unit of nothing but `extend` blocks exports methods rather than names, and an empty public surface is one declaration away from changing. The import itself still did its work for this unit. Drop the `public`, or make the imported unit export something."))
