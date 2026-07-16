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

# CE4008/CE4009 (generic-perk implementation arity) were registered speculatively
# for a generic-perk feature that never landed; CE4010 now rejects generic perks
# at the declaration, so those two codes became unreachable by construction and
# were removed. If generic perks ever land, mint fresh codes.

_add(ErrorMessage("CE4010", Severity.ERROR,
    "perk {name} cannot have type parameters",
    Category.PERK, "Perks cannot be generic. Remove the <...> type parameter list; constrain generic functions with '<T: {name}>' instead."))
