"""SHIR to SLIR: a cast, and the call of a declared conversion (docs/design/ir.md 8.9).

A numeric cast, and a cast to the type of the operand, is a `Cast` rvalue. A conversion
(`extend A as B:`) is a `Direct` call of the symbol of its body, with ONE `Owned` operand:
the body takes `nom self`. `e as B` is that call, and the `Err` path of a `??` across two
error types calls it with the error that it moves out of the wrapper. Each arm is a free
function that takes the `Lowering` of `lower.py` first. This module reads `lower.py` for
its types only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.semantics.conversions import conversion_symbol
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Conversion
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.lower_values import CallValue
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering
    from sushi_lang.semantics.slir.lower_values import Value


def cast(low: Lowering, node: e.Cast) -> Value:
    """A numeric cast or an identity cast is a `Cast`; a cast that calls a conversion is
    the call of the conversion."""
    choice = low.typeck.conversion_of(node.shir_id)
    operand = low.operand(node.operand)
    if isinstance(choice, Conversion):
        return convert(low, choice, operand, low.type_of(node.operand), low.type_of(node))
    return r.Cast(operand, low.type_of(node))


def convert(low: Lowering, choice: Conversion, operand: p.Operand, source: Type,
            target: Type) -> CallValue:
    """The call of the conversion from `source` to `target`, which takes `operand`."""
    return CallValue(st.Direct(conversion_symbol(source, target), low.decls.name_of(choice.decl)),
                     ((operand, Convention.OWNED),))
