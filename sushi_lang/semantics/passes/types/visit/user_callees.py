"""The callee row of a function, a method or a static that a unit declares (ir.md 7.3, 8.6).

A declaration of the build names the unit that declares it (C5).
"""
from __future__ import annotations

from typing import Optional, Protocol

from sushi_lang.semantics.never_return import never_returns
from sushi_lang.semantics.shir.callee import Callee
from sushi_lang.semantics.shir.typeck import ResolvedCall, Tail
from sushi_lang.semantics.typesys import Type


class Declared(Protocol):
    """A signature of a function or of an extension method."""
    unit_name: Optional[str]


def user_callee(callee: Callee, declared: Declared, type_args: tuple[Type, ...] = (),
                tail: Optional[Tail] = None) -> ResolvedCall:
    """The row of `callee`: the unit of `declared`, and whether the call never returns (a
    `!` return, docs/design/never-return.md)."""
    never = never_returns(declared)
    return ResolvedCall(callee, type_args, declared.unit_name, tail=tail, never_returns=never)
