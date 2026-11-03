"""SHIR to SLIR: a value before lowering knows the place that takes it (docs/design/ir.md 8.9).

An arm of a value answers an rvalue, a call, or a short circuit; the store of the value
into its place makes the statement (`Lowering.write`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st


@dataclass(frozen=True, slots=True)
class CallValue:
    """A call, before lowering knows the place that takes its value."""
    callee: st.SlirCallee
    args: tuple[tuple[p.Operand, Convention], ...]


@dataclass(frozen=True, slots=True)
class ShortCircuit:
    """`and` or `or`, before lowering knows the place that takes its value: the left operand,
    and the right operand that a block of its own evaluates (ir.md 8.9)."""
    op: str
    lhs: p.Operand
    rhs: e.ShirExpr
    span: Span


Value: TypeAlias = r.Rvalue | CallValue | ShortCircuit
