"""The SHIR ids and the one counter that gives them (docs/design/ir.md 7.2, 7.3, 11.3).

An id is an index from a counter that its allocator owns. Ids are given in the order of
the calls, so a fixed visiting order gives the same ids on every run. An id never comes
from `id()` or from the order of a hash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar


@dataclass(frozen=True, slots=True, order=True)
class ShirId:
    """A SHIR node. Unique in one body."""
    index: int


@dataclass(frozen=True, slots=True, order=True)
class LocalId:
    """A local of one body: a parameter, a binder, or a hidden local."""
    index: int


@dataclass(frozen=True, slots=True, order=True)
class DeclId:
    """A declaration of the program: a function, a constant, a `var` or a type."""
    index: int


IdT = TypeVar("IdT")


@dataclass(slots=True)
class IdAllocator(Generic[IdT]):
    """Gives the ids of one kind: 0, 1, 2 and up, in the order of the calls."""
    kind: Callable[[int], IdT]
    _next: int = field(default=0, init=False)

    def allocate(self) -> IdT:
        ident = self.kind(self._next)
        self._next += 1
        return ident
