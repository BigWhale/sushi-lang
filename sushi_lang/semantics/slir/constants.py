"""The SLIR constants (docs/design/ir.md 8.5).

The constant evaluator makes a constant on the AST (ruling Q5), and lowering carries it
through unchanged. Only the backend turns one into an LLVM constant.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sushi_lang.semantics.typesys import Type


@dataclass(frozen=True, slots=True)
class Int:
    value: int
    ty: Type


@dataclass(frozen=True, slots=True)
class Float:
    value: float
    ty: Type


@dataclass(frozen=True, slots=True)
class Bool:
    value: bool


@dataclass(frozen=True, slots=True)
class Blank:
    """`~`."""


@dataclass(frozen=True, slots=True)
class Str:
    """A string literal, as UTF-8 bytes. It owns no heap."""
    value: bytes


@dataclass(frozen=True, slots=True)
class Aggregate:
    """A constant array, struct or enum variant."""
    ty: Type
    parts: tuple[Constant, ...]


@dataclass(frozen=True, slots=True)
class FnRef:
    symbol: str


Constant: TypeAlias = Int | Float | Bool | Blank | Str | Aggregate | FnRef
