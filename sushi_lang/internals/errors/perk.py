"""Perk errors (CE4xxx)."""
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
    Category.PERK, "The perk is not in the scope of this unit, in an implementation, a constraint or a pack constraint. When no unit declares the perk, define it with 'perk {perk}:'. When another unit or a library declares it, the help names the import that brings it: scope is per unit and not transitive, so a plain `use` in another unit does not bring it here (#1124). The predefined perks are in every scope."))

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
    Category.PERK, "Perks cannot be generic. Remove the @(...) type parameter list; constrain generic functions with '@(T: {name})' instead."))

_add(ErrorMessage("CE4011", Severity.ERROR,
    "cannot {action} private perk '{name}' from unit '{current_unit}' (perk is defined in '{owner}')",
    Category.PERK, "Ruling 3 of `docs/design/visibility.md`: a perk carries `public` and is private by default, and what a private perk hides is the CONTRACT. Another unit may not implement it and may not constrain a type parameter with it, because both of those are promises about the perk itself. Calling a method it provides is untouched: method resolution is blind to the caller, so a unit that can name the type can call what the type implements. This is a different rule from CE3005, which is about naming a declaration; a perk contract has its own code because it has its own answer -- the method stays reachable while the contract does not. Mark the perk `public`, or ask its unit for a function that does the work."))

_add(ErrorMessage("CE4012", Severity.ERROR,
    "'Drop' cannot be implemented for '{type}' here: only unit '{owner}' declares that type",
    Category.PERK, "HANDLES.md ruling R2b: the orphan rule, narrowed to one perk. `PerkImplementationTable.replace` lets a consumer's `extend X with P` take over a library's implementation, which is the sanctioned override of decision 11 in `docs/design/visibility.md`. For an ordinary perk that is a feature. For `Drop` it lets a consumer silently stop a handle from closing, so the type that owns a resource is the only one allowed to say what releasing it means. It also bounds the incremental-cache problem: with the rule the declaration and the implementation are in one unit, so one unit's AST hash covers both. Add the implementation to the unit that declares the type, or ask that unit for a function that does the work."))

# CE4013 refused `Drop` on a GENERIC target, because the implementation registered
# under a key carrying the type-parameter names and no concrete instance matched it --
# a silent no-op, worse than a refusal. A generic-target perk implementation is a
# TEMPLATE now, instantiated once per instantiation the program names and registered
# under the interned name, so the key a concrete receiver resolves to is the key the
# implementation is filed under. The rule had nothing left to refuse and the code was
# retired. `extend BufWriter@(W) with Drop` is what needed it.

_add(ErrorMessage("CE4014", Severity.ERROR,
    "perk '{perk}' cannot hold the static method '{method}'",
    Category.PERK, "HANDLES.md ruling R7: a perk has no `Self`, so a contract cannot say 'returns one of me' and a constructor has no signature to declare there. #542 ruling R1 keeps the refusal a COMPILER error rather than a parse error: the grammar admits the marker in the implementation position precisely so this diagnostic can point at it and say why. Declare the static as a plain extension method on the type (`extend Vec static at(...)`) -- it is as visible as the type either way -- and leave the perk to the instance methods it can contract."))

_add(ErrorMessage("CE4016", Severity.ERROR,
    "'Drop' cannot be implemented for '{type}': no unit declares that type",
    Category.PERK, "The orphan rule of CE4012, at a type no unit declares: a primitive, a `string`, a fixed or a dynamic array (an array template `extend T[]` included), `List`, `HashMap`, `Own`, `Maybe`, `Result` and the predefined error enums. Only the unit that declares a type may say what releasing it means, and no unit declares one of these. Before this code the implementation was accepted and `drop()` never ran, because the compiler gives these types their own release. A resource needs a type of its own: declare a struct that holds the value and implement `Drop` on that struct."))

_add(ErrorMessage("CE4015", Severity.ERROR,
    "perk '{perk}' gives '{method}' a second home: perk '{other}' already provides it",
    Category.PERK, "A name has exactly one home on a type (`docs/design/method-resolution.md`). Two perks that each provide a method of one name on one type leave a call of that name naming neither implementation, and the two bodies would take one symbol. The note points at the first one. Rename the method of one perk, or implement only one of them. A derived method is not a home: a type that derives `compare` from `Ord` may still implement a user perk that provides `compare`, and an explicit call then reads the implementation."))
