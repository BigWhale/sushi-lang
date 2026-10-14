"""The SHIR pattern nodes, the binding of a pattern and the match arm (docs/design/ir.md 7.8).

Data only. A pattern holds what the source says: a literal and a range bound keep their
written values, because the value that a bound gives depends on the type at its position,
and that is an answer of `typecheck` (ruling S5). The union `ShirPattern` is every pattern
node, and a dispatch reads it (gate: `tests/unit/test_shir_node_unions_are_total.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sushi_lang.semantics.shir.exprs import Constant
from sushi_lang.semantics.shir.ids import DeclId, LocalId
from sushi_lang.semantics.shir.nodes import ShirBlock, ShirNode
from sushi_lang.semantics.shir.stmts import Bare, Ref


@dataclass(frozen=True, slots=True)
class Nom:
    """A `nom` binding: the arm takes the payload. The match must own the scrutinee."""


BindMode: TypeAlias = Bare | Ref | Nom


@dataclass(frozen=True, slots=True)
class PatBinding:
    """A name that a pattern binds. Two alternatives of `|` give one name one local."""
    local: LocalId
    by: BindMode


@dataclass(slots=True)
class Wildcard(ShirNode):
    """`_`."""


@dataclass(slots=True)
class Literal(ShirNode):
    """An integer or a string arm."""
    value: Constant


@dataclass(slots=True)
class IntRange(ShirNode):
    """`0x80..=0x8f`: an integer range. `inclusive` is the `..=` form."""
    low: Constant
    high: Constant
    inclusive: bool


@dataclass(slots=True)
class Variant(ShirNode):
    """`Shape.Circle(r)`: one item for each value of the payload."""
    decl: DeclId
    variant: str
    items: list[ShirPattern | PatBinding]


@dataclass(slots=True)
class Tuple(ShirNode):
    elements: list[ShirPattern | PatBinding]


@dataclass(slots=True)
class Own(ShirNode):
    """`Own(poke h)`: it unwraps an `Own@(T)`."""
    inner: ShirPattern | PatBinding


@dataclass(slots=True)
class Or(ShirNode):
    """`1 | 2`: two or more alternatives. Each one binds the same names (CE2126)."""
    alternatives: list[ShirPattern | PatBinding]


ShirPattern: TypeAlias = Wildcard | Literal | IntRange | Variant | Tuple | Own | Or


@dataclass(frozen=True, slots=True)
class Arm:
    """A match arm. An arm that the source writes as one expression has a block of one."""
    pattern: ShirPattern
    body: ShirBlock
