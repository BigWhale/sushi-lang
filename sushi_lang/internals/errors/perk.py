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

_add(ErrorMessage("CE4011", Severity.ERROR,
    "cannot {action} private perk '{name}' from unit '{current_unit}' (perk is defined in '{owner}')",
    Category.PERK, "Ruling 3 of `docs/design/visibility.md`: a perk carries `public` and is private by default, and what a private perk hides is the CONTRACT. Another unit may not implement it and may not constrain a type parameter with it, because both of those are promises about the perk itself. Calling a method it provides is untouched: method resolution is blind to the caller, so a unit that can name the type can call what the type implements. This is a different rule from CE3005, which is about naming a declaration; a perk contract has its own code because it has its own answer -- the method stays reachable while the contract does not. Mark the perk `public`, or ask its unit for a function that does the work."))

_add(ErrorMessage("CE4012", Severity.ERROR,
    "'Drop' cannot be implemented for '{type}' here: only unit '{owner}' declares that type",
    Category.PERK, "HANDLES.md ruling R2b: the orphan rule, narrowed to one perk. `PerkImplementationTable.replace` lets a consumer's `extend X with P` take over a library's implementation, which is the sanctioned override of decision 11 in `docs/design/visibility.md`. For an ordinary perk that is a feature. For `Drop` it lets a consumer silently stop a handle from closing, so the type that owns a resource is the only one allowed to say what releasing it means. It also bounds the incremental-cache problem: with the rule the declaration and the implementation are in one unit, so one unit's AST hash covers both. Add the implementation to the unit that declares the type, or ask that unit for a function that does the work."))

_add(ErrorMessage("CE4013", Severity.ERROR,
    "'Drop' cannot be implemented for a generic target '{type}'",
    Category.PERK, "A perk implementation is keyed by the name of the type it targets, and a generic target's key carries the TYPE-PARAMETER names rather than a concrete type argument, so no concrete instance matches it. So `extend Box@(T) with Drop` would register an implementation that never fires, and a value that looks like it releases a resource would silently leak it. A silent no-op is worse than a refusal, so v1 refuses. Implement `Drop` on each concrete instantiation that needs it, or let the generic's owning FIELDS carry the ownership -- a `BufReader@(R)` holding an owning R needs no Drop of its own, because destroying its fields destroys the handle."))
