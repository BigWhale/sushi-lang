"""The walk that a loop gets (docs/design/ir.md 7.7).

The `typecheck` pass picks the kind of each `Loop` once, from the types, and records it in
`TypeckResults` (ruling S5). A kind holds no operand: the `LoopSource` of the node holds
what the source says. Data only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sushi_lang.semantics.shir.callee import Callee


@dataclass(frozen=True, slots=True)
class Keys:
    """`.keys()`."""


@dataclass(frozen=True, slots=True)
class Values:
    """`.values()`."""


@dataclass(frozen=True, slots=True)
class Entries:
    """`.entries()`."""


@dataclass(frozen=True, slots=True)
class Pairs:
    """`.pairs()`."""


HashMapView: TypeAlias = Keys | Values | Entries | Pairs


@dataclass(frozen=True, slots=True)
class While:
    """The kind of every `Cond` source."""


@dataclass(frozen=True, slots=True)
class CountedRange:
    """A range written as the iterable."""


@dataclass(frozen=True, slots=True)
class Cursor:
    """A direct walk of contiguous elements: `T[]`, `T[N]`, `List@(T)`, a slice."""


@dataclass(frozen=True, slots=True)
class HashMapCursor:
    """A direct walk of one view of a HashMap."""
    view: HashMapView


@dataclass(frozen=True, slots=True)
class Protocol:
    """A walk of a type that carries `next()`: `next` is the callee of that method."""
    next: Callee


@dataclass(frozen=True, slots=True)
class IteratorValue:
    """A walk of an `Iterator@(T)` value, through its `next_fn`."""


LoopKind: TypeAlias = While | CountedRange | Cursor | HashMapCursor | Protocol | IteratorValue
