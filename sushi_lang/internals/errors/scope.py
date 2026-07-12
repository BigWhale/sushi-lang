"""Scope and variable errors (CE1xxx).

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


# Scope errors
_add(ErrorMessage("CE1001", Severity.ERROR,
    "use of undeclared identifier '{name}'",
    Category.SCOPE, "The identifier was used before it was declared or is not in scope."
))

_add(ErrorMessage("CE1002", Severity.ERROR,
    "assignment to undeclared variable '{name}'",
    Category.SCOPE, "Use 'let' to declare a variable before reassigning it with ':='."))

_add(ErrorMessage("CE1003", Severity.ERROR,
    "not allowed here (must be inside a loop).",
    Category.SCOPE, "Emitted when 'break' or 'continue' appear outside any loop."))

_add(ErrorMessage("CE1004", Severity.ERROR,
    "variable {name} shadows the loop condition.",
    Category.SCOPE, "Emitted when declaring let name inside a loop body when name is read in the loop condition."))
