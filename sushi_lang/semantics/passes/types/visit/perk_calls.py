"""A call of a method of a perk implementation in the typecheck pass over SHIR (ir.md 7.6).

It is a `Method` callee, as an extension method is. In an instance the type of the receiver
is concrete (Q1), so the call names one implementation. In a template check the method of an
opaque receiver is the method that a constraint promises. The declaring unit stays empty: a
perk method is unique in a program, and its symbol has no unit.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.param_modes import receiver_mode
from sushi_lang.semantics.passes.types.calls.methods import reject_call_of_drop
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Method
from sushi_lang.semantics.shir.typeck import ResolvedCall
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_perk_call(validator: TypeValidator, node: e.Call, target: e.Dotted,
                    receiver: Type) -> Optional[Type]:
    """The callee row of the perk method `target.name`, and the arguments of its call. A
    written call of `drop` is CE4020. A wrong count says no more about the arguments."""
    from sushi_lang.semantics.passes.types.visit.calls import check_method_arguments
    if reject_call_of_drop(validator, node.loc, receiver, target.name):
        return None
    method = validator.perk_impl_table.get_method(receiver, target.name)
    callee = Method(target.recv, validator.method_decl(receiver, target.name),
                    receiver_mode(method.self_mode).convention)
    validator.body_answers().record_callee(node.shir_id, ResolvedCall(callee))
    return check_method_arguments(validator, node, receiver, target.name, method, callee,
                                  stop_on_arity=True)
