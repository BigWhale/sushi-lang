"""Borrow and reference errors (CE24xx).

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


_add(ErrorMessage("CE2410", Severity.ERROR,
    "cannot move '{name}': it is a borrowed view of the process arguments (main's string[] args); borrow it instead with '&peek string[]'",
    Category.BORROW, "main's `string[] args` aliases the process argv, which the runtime owns and frees. Moving it by value (passing it to a by-value parameter, rebinding, or storing it) would make the callee free argv and double-free. Take it by reference with `&peek string[]`."))

# Borrow/reference errors (CE24xx)
_add(ErrorMessage("CE2400", Severity.ERROR,
    "cannot borrow '{name}': variable does not exist",
    Category.BORROW, "Attempted to borrow a variable that was not declared."))

_add(ErrorMessage("CE2401", Severity.ERROR,
    "cannot move/reassign '{name}' while it is borrowed",
    Category.BORROW, "A variable cannot be moved or reassigned while a reference to it is active."))

_add(ErrorMessage("CE2402", Severity.ERROR,
    "cannot destroy '{name}' while it is borrowed",
    Category.BORROW, "A variable cannot be explicitly destroyed (.destroy()) while a reference to it is active."))

_add(ErrorMessage("CE2403", Severity.ERROR,
    "'{name}' already has an active &poke borrow (only one exclusive borrow allowed)",
    Category.BORROW, "A variable can only have one active &poke (read-write) borrow at a time to prevent aliasing issues."))

_add(ErrorMessage("CE2404", Severity.ERROR,
    "cannot borrow '{expr}': expression has no stable address",
    Category.BORROW, "The borrow operator (&) can only be applied to variables and struct member access (e.g., &x, &obj.field), not temporary values or function call results."))

_add(ErrorMessage("CE2405", Severity.ERROR,
    "cannot borrow moved variable '{name}'",
    Category.BORROW, "Attempted to borrow a variable whose ownership has been transferred elsewhere."))

_add(ErrorMessage("CE2406", Severity.ERROR,
    "use of destroyed variable '{name}'",
    Category.BORROW, "Variable was explicitly destroyed via .destroy() and is no longer valid."))

_add(ErrorMessage("CE2407", Severity.ERROR,
    "cannot have &peek and &poke borrows of '{name}' simultaneously",
    Category.BORROW, "A variable cannot have both read-only (&peek) and read-write (&poke) borrows at the same time."))

_add(ErrorMessage("CE2408", Severity.ERROR,
    "cannot modify '{name}' through &peek reference (read-only)",
    Category.BORROW, "&peek references are read-only. Use &poke for mutable access."))
