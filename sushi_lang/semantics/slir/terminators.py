"""The five SLIR terminators (docs/design/ir.md 8.8).

A call is not a terminator (ruling R3). Every terminator carries a span (G9).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TypeAlias

from sushi_lang.internals.report import Span
from sushi_lang.semantics.slir.ids import BlockId
from sushi_lang.semantics.slir.places import Operand


@dataclass(frozen=True, slots=True)
class Goto:
    target: BlockId
    span: Span


@dataclass(frozen=True, slots=True)
class SwitchInt:
    """`otherwise` is None when no other value can reach the switch (the tag of a `Result`)."""
    operand: Operand
    targets: tuple[tuple[int, BlockId], ...]
    otherwise: Optional[BlockId]
    span: Span


@dataclass(frozen=True, slots=True)
class Return:
    span: Span


@dataclass(frozen=True, slots=True)
class Trap:
    """A run-time trap: the registered RExxxx code and the values that its text prints."""
    code: str
    args: tuple[Operand, ...]
    span: Span


@dataclass(frozen=True, slots=True)
class Unreachable:
    span: Span


SlirTerminator: TypeAlias = Goto | SwitchInt | Return | Trap | Unreachable
