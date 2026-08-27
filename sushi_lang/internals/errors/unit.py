"""Unit management errors (CE3xxx)."""
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
    "cannot {verb} private {kind} '{name}' from unit '{current_unit}' ({kind} is defined in '{owner}')",
    Category.UNIT, "A private declaration can only be named from within the unit that declares it. Mark it `public` to let another unit name it. The `{kind}` and `{verb}` fields carry which kind of declaration it was, so one gate answers for a function, a constant, a struct and an enum rather than four. A generic is no exception (#467): a source library's units are ordinary units at the consumer, so a private generic of one resolves like any other symbol, and this is where it is refused. Before that the only place that noticed was the backend, which had no template to emit. A binary library answers here too (#469): the manifest names what the library declares and does not export, so a name that reaches the consumer's tables not at all is still private and not undefined. `{owner}` is then the library rather than a unit."))

_add(ErrorMessage("CE3006", Severity.ERROR,
    "unknown stdlib module <{module}>",
    Category.UNIT, "The imported standard-library module does not exist. Check the spelling against the available modules."))

# Producing the output. Both are user or environment conditions, not compiler bugs,
# which is why neither may reach the CE0000 top-level guard.
_add(ErrorMessage("CE3007", Severity.ERROR,
    "no main() function: an executable needs an entry point",
    Category.UNIT, "A program compiled without --lib is linked into an executable, and the linker needs a main(). Add one, or compile the unit as a library with --lib."))

_add(ErrorMessage("CE3008", Severity.ERROR,
    "linking failed: '{cc}' exited with status {status}",
    Category.UNIT, "The C compiler used as the linker rejected the object file. Its own output is attached as a note. This is an environment condition, not a compiler bug."))

_add(ErrorMessage("CE3011", Severity.ERROR,
    "cannot declare {kind} '{name}': '{owner}' declares it too",
    Category.UNIT, "Names are flat across a program. A source library's units and a bundled stdlib module are ordinary compilation units at the consumer, so a name either of them declares is a name the consumer cannot declare again. The compiler used to let the consumer's declaration replace the library's without saying anything, which is not safe in one namespace: the library's own bodies then call the consumer's function. In practice it was worse, because the replacement was never registered -- the consumer lost its own declaration as well, and heard CE3005 about a private it wrote itself, or CE2027 about a struct shape it never spelled. Rename your declaration. `docs/design/unit-namespaces.md` carries the qualified-name design that would lift this."))
