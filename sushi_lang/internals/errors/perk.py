"""Perk errors (CE4xxx).

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


# Perk-related errors (CE4xxx)
_add(ErrorMessage("CE4001", Severity.ERROR,
    "duplicate perk definition: {name}",
    Category.PERK, "A perk with this name has already been defined. Each perk must have a unique name."))

_add(ErrorMessage("CE4002", Severity.ERROR,
    "type {type} already implements perk {perk}",
    Category.PERK, "A perk can only be implemented once for each type. Remove the duplicate implementation."))

_add(ErrorMessage("CE4003", Severity.ERROR,
    "unknown perk: {perk}",
    Category.PERK, "The perk being implemented has not been defined. Define the perk with 'perk {perk}:' before implementing it."))

_add(ErrorMessage("CE4004", Severity.ERROR,
    "method {method} signature does not match perk {perk} requirement",
    Category.PERK, "The implementation method signature must exactly match the signature declared in the perk definition."))

_add(ErrorMessage("CE4005", Severity.ERROR,
    "missing required method {method} for perk {perk}",
    Category.PERK, "The perk implementation is missing a required method. All methods declared in the perk must be implemented."))

_add(ErrorMessage("CE4006", Severity.ERROR,
    "type {type} does not implement perk {perk} required by constraint",
    Category.PERK, "A type constraint requires the type to implement a specific perk. Add an implementation with 'extend {type} with {perk}:'."))

_add(ErrorMessage("CE4007", Severity.ERROR,
    "method {method} conflicts with perk method from {perk}",
    Category.PERK, "A regular extension method has the same name as a perk method. Rename one of the methods to avoid ambiguity."))

_add(ErrorMessage("CE4008", Severity.ERROR,
    "cannot implement perk {perk} for type {type}: perk is generic but no type arguments provided",
    Category.PERK, "Generic perks require type arguments when implemented. Use 'extend {type} with {perk}<T>:' syntax."))

_add(ErrorMessage("CE4009", Severity.ERROR,
    "perk {perk} requires {expected} type arguments, got {actual}",
    Category.PERK, "The number of type arguments provided does not match the perk definition."))
