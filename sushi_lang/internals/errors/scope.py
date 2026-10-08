"""Scope and variable errors (CE1xxx)."""
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

_add(ErrorMessage("CE1005", Severity.ERROR,
    "{kind} '{name}' already declared in this unit as {other}",
    Category.SCOPE, "In one unit, one name has one declaration, whatever its kind: `fn`, `const`, `var`, `struct`, `enum` and `perk` share one set of names (#1076). The SECOND declaration in source order is the error, and the note points at the first. The first keeps the name, and the refused declaration enters no table, so the uses of the first give no more errors. A use of the name that finds nothing under it -- a call of a refused function, a type position or a construction of a refused struct -- is a use of the refused declaration, and it gives no second diagnostic either (#1102). Two declarations of ONE kind keep that kind's code: CE0004 (struct), CE2046 (enum), CE4001 (perk), CE0101 (fn) and CE0105 (const and var), and a struct beside an enum is CE0006. Across units a name may be used again: the unit's own declaration wins over a name a flat `use` brings, and two imported public names of one spelling are reached with `use ... as` (CE3012 at a bare use). Two TYPES of one name in two units stay refused, because a type is one per program. Before this rule a `const` beside a `fn` crashed the backend with CE0000 (the two took one symbol), and a `struct` beside a `fn` took every call of the name. Rename one of the two declarations."))

_add(ErrorMessage("CE1006", Severity.ERROR,
    "'{name}' is already declared in this scope",
    Category.SCOPE, "One scope declares a local name one time. A `let`, a destructure binder, a pattern binding, a `foreach` item, an `expand` binder and a parameter share the scope of the body they start, so a `let` in a `foreach` body cannot repeat the item name, and a `let` in a function body cannot repeat a parameter name. The SECOND declaration is the error, and the note points at the first. The first keeps the name. To change the value, write `:=`. To use the name again for a different value, open a nested block: a declaration there shadows the outer one (CW1002). A repeated parameter name of a function or a lambda is CE0102. Before this rule the second declaration crashed the backend with CE0000, or silently replaced the first in an `expand` body and in a pattern (#1197)."))
