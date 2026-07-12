"""Unit management errors (CE3xxx).

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


# Unit Management Errors (CE3xxx)
_add(ErrorMessage("CE3001", Severity.ERROR,
    "circular dependency detected: {cycle}",
    Category.UNIT, "Units have circular dependencies that prevent compilation ordering."))

_add(ErrorMessage("CE3002", Severity.ERROR,
    "unit '{name}' not found (expected: {path})",
    Category.UNIT, "A required unit file could not be found at the expected location."))

_add(ErrorMessage("CE3003", Severity.ERROR,
    "duplicate public symbol '{symbol}' found in units: {units}",
    Category.UNIT, "Multiple units export the same public symbol name, creating an ambiguity."))

_add(ErrorMessage("CE3004", Severity.ERROR,
    "invalid unit path '{path}': {reason}",
    Category.UNIT, "Unit path contains invalid characters or structure."))

_add(ErrorMessage("CE3005", Severity.ERROR,
    "cannot call private function '{name}' from unit '{current_unit}' (function is defined in '{func_unit}')",
    Category.UNIT, "Private functions can only be called from within the same unit. Use 'public fn' to make the function accessible across units."))

_add(ErrorMessage("CE3006", Severity.ERROR,
    "unknown stdlib module <{module}>",
    Category.UNIT, "The imported standard-library module does not exist. Check the spelling against the available modules."))
