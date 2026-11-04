"""The SLIR places and operands (docs/design/ir.md 8.4).

A place is storage: a local, a unit-level `var`, or a projection of a place. An operand
reads a place or holds a constant. `Copy` and `Move` are the answer of the `borrow` pass
(`classify()` runs one time there), and lowering writes that answer into the operand.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sushi_lang.semantics.slir.constants import Constant
from sushi_lang.semantics.slir.ids import LocalId
from sushi_lang.semantics.typesys import Type


@dataclass(frozen=True, slots=True)
class Local:
    id: LocalId


@dataclass(frozen=True, slots=True)
class Global:
    """A unit-level `var`, and its declared type. It is never moved out of
    (docs/design/unit-storage.md)."""
    symbol: str
    ty: Type


@dataclass(frozen=True, slots=True)
class Field:
    base: Place
    index: int


@dataclass(frozen=True, slots=True)
class Index:
    base: Place
    index: Operand


@dataclass(frozen=True, slots=True)
class Downcast:
    """The enum in `base`, read as the variant `variant`: valid only under that variant."""
    base: Place
    variant: str


@dataclass(frozen=True, slots=True)
class Deref:
    base: Place


Place: TypeAlias = Local | Global | Field | Index | Downcast | Deref


@dataclass(frozen=True, slots=True)
class Copy:
    """The value is plain, or the source is a borrow."""
    place: Place


@dataclass(frozen=True, slots=True)
class Move:
    """Ownership goes to the reader; the source is dead after it."""
    place: Place


@dataclass(frozen=True, slots=True)
class Const:
    constant: Constant


Operand: TypeAlias = Copy | Move | Const
