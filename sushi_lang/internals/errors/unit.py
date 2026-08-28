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
    Category.UNIT, "Multiple units export the same public symbol name, creating an ambiguity. Names are flat across a program, so one exported name has one owner. Rename one of them, or keep one of them private. A note points at each declaration."))

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

_add(ErrorMessage("CE3009", Severity.ERROR,
    "public {kind} '{name}' names private type '{type}'",
    Category.UNIT, "Privacy on a type is worth nothing if a public signature hands the type out anyway: a consumer would receive a value of a type it cannot name, declare or construct. Rust answers the same condition with E0446. The rule reads what the signature SPELLS -- the return, the error arm, every parameter, a constant's type, a public struct's field and a public enum's variant payload -- and it follows a type argument, an array element, a borrow and a function type into what they carry. What a named type holds is that type's own declaration's business and is fenced there, so a public struct holding a private field hears this once, at the field. Mark the type `public`, or make the declaration that names it private."))

_add(ErrorMessage("CE3010", Severity.ERROR,
    "public {kind} '{name}' constrains a type parameter with private perk '{perk}'",
    Category.UNIT, "A constraint is part of a signature: a consumer that calls a generic has to satisfy the constraint, which means naming the perk. Rust answers this with E0445. It is not CE4011, and the difference is the direction: CE4011 is a USE-site rule -- the perk is not nameable in that unit at all -- while this is a LEAK rule, where the perk is nameable right there in its own unit and the signature would hand it to a unit where it is not. Mark the perk `public`, or make the generic private."))

_add(ErrorMessage("CE3011", Severity.ERROR,
    "cannot declare {kind} '{name}': '{owner}' declares it too",
    Category.UNIT, "Names are flat across a program. A source library's units and a bundled stdlib module are ordinary compilation units at the consumer, so a name either of them declares is a name the consumer cannot declare again. The compiler used to let the consumer's declaration replace the library's without saying anything, which is not safe in one namespace: the library's own bodies then call the consumer's function. In practice it was worse, because the replacement was never registered -- the consumer lost its own declaration as well, and heard CE3005 about a private it wrote itself, or CE2027 about a struct shape it never spelled. Rename your declaration. `docs/design/unit-namespaces.md` carries the qualified-name design that would lift this."))

# CE3012 (an unqualified name offered by more than one flat import) is the third code
# `docs/design/unit-namespaces.md` section 10 reserves. It arrives with the per-unit
# scope that creates the condition; nothing can offer two candidates until then.

_add(ErrorMessage("CE3013", Severity.ERROR,
    "'{alias}' is already bound in this unit",
    Category.UNIT, "An alias binds a name in the unit that wrote it, so it collides with anything else that unit binds: another alias, an `unsafe external` namespace, or one of its own declarations. Two aliases for one import are legal and both work; one name holding two namespaces is not, because a qualified name would have two answers. The note points at what bound the name first. `_` is refused for the same reason: the language binds it as the discard name, so it cannot name a namespace. Rename the alias."))

_add(ErrorMessage("CE3014", Severity.ERROR,
    "a `use` must come before every declaration",
    Category.UNIT, "Every import stands at the top of the unit, after the unit's own doc block if it has one, and a namespace is bound for the whole unit rather than from its `use` downwards. The two halves answer one question today's grammar leaves open in both directions: a `use` is a toplevel, so it may sit anywhere, and a declaration is already order-independent. Go and Java both make the placement mandatory; Rust leaves it to convention. Sushi follows Go and Java, so a reader sees a unit's dependencies in one block. Move the `use` above the first declaration."))
