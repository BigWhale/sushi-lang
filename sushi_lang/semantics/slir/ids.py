"""The SLIR ids (docs/design/ir.md 8.2, 8.11).

SLIR counts its own ids. `_0` is the return slot, `_1` to `_n` are the parameters, and the
other locals follow in the order that lowering makes them. So a SLIR id of a local is not
the SHIR id of that local.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class LocalId:
    """A local of one function: the return slot, a parameter, a named local or a temporary."""
    index: int


@dataclass(frozen=True, slots=True, order=True)
class BlockId:
    """A basic block of one function."""
    index: int


@dataclass(frozen=True, slots=True, order=True)
class BorrowId:
    """A borrow region of one function: a `BeginBorrow` and its `EndBorrow`."""
    index: int
