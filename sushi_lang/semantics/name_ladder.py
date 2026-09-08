"""What a BARE name in an expression denotes here: section 8's ladder, in ONE place.

`docs/design/unit-namespaces.md` section 8 gives an unqualified name one ordered ladder,
and the compiler had two implementations of it -- the scope pass's and the typecheck
pass's. The two disagreed at the TYPE rung: an enum name written where a value belongs
was "not a variable, so not my fault" to both passes, escaped semantics whole, and died
in the emitter as CE0055, which tells the user their own mistake is a compiler bug and
gives them no file, no line and no caret (#600).

The ORDER lives here. The LOOKUPS stay each pass's own, because no two passes reach the
tables the same way: the scope pass asks its collect tables and its scope stack, and the
typecheck pass asks a unit-scoped `lookup` per table. A consumer supplies a `Rungs`
object, one method per rung, and `classify` walks them top to bottom.
`tests/unit/test_bare_name_ladder_is_one.py` is the gate: every rung is reachable, and a
higher rung wins over a lower one.
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol


class BareName(Enum):
    """What an unqualified name denotes. Closed, and every member is a rung."""

    LOCAL = "local"
    CONSTANT = "constant"
    STDLIB_CONSTANT = "stdlib constant"
    FUNCTION = "function"
    NAMESPACE = "namespace"
    TYPE = "type"
    NOTHING = "nothing"


# Section 8's ladder, top to bottom. `NOTHING` is not a rung: it is the answer when no
# rung claims the name, and it is the one answer that means the name is undeclared.
RUNGS: tuple[BareName, ...] = (
    BareName.LOCAL,
    BareName.CONSTANT,
    BareName.STDLIB_CONSTANT,
    BareName.FUNCTION,
    BareName.NAMESPACE,
    BareName.TYPE,
)


class Rungs(Protocol):
    """One question per rung of `RUNGS`. A consumer answers all of them.

    A Protocol rather than a mapping of callables: a consumer that cannot answer a rung
    fails to type-check, where a missing key would silently skip the rung -- which is
    the shape #600 was.
    """

    def is_local(self, name: str) -> bool:
        """Row 1: a variable or a parameter in an active scope. It wins over every rung
        below, which is #296's rule."""

    def is_constant(self, name: str) -> bool:
        """A `const` or a unit `var` this unit may write, its own or an imported one."""

    def is_stdlib_constant(self, name: str) -> bool:
        """A registry module's constant a flat `use <module>` brought (#560)."""

    def is_function(self, name: str) -> bool:
        """A function this unit may call, generic or plain, referenced as a value."""

    def is_namespace(self, name: str) -> bool:
        """A `use ... as` alias or an `unsafe external` block's namespace."""

    def is_type(self, name: str) -> bool:
        """A type name: a struct, an enum, either one's generic form, or a primitive."""


def classify(name: str, rungs: Rungs) -> BareName:
    """The rung `name` reaches, walking `RUNGS` top to bottom."""
    if rungs.is_local(name):
        return BareName.LOCAL
    if rungs.is_constant(name):
        return BareName.CONSTANT
    if rungs.is_stdlib_constant(name):
        return BareName.STDLIB_CONSTANT
    if rungs.is_function(name):
        return BareName.FUNCTION
    if rungs.is_namespace(name):
        return BareName.NAMESPACE
    if rungs.is_type(name):
        return BareName.TYPE
    return BareName.NOTHING


# The rungs that are not a value. A name at one of them is declared, so "undeclared
# identifier" is the wrong word for it, and a pass that walks an expression has to say
# what the name IS instead of falling through to the back end.
NOT_A_VALUE: frozenset[BareName] = frozenset({BareName.NAMESPACE, BareName.TYPE})
