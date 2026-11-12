"""The arm of a built-in method with a signature row in the typecheck pass over SHIR:
`or_err` of `Maybe@(T)` and `map_err` of `Result@(T, E)` (docs/design/ir.md 7.3, 8.3).

The method has a method-level type parameter, so its argument solves the instance. The
questions come in the order of `main`: a wrong count is CE2009, and the call answers
nothing more. Then the argument is typed with no type from its position, and the solver
reads its type (`settle_builtin_instance`): a type parameter that stays unsolved is
CE2063, and a solved one in an `E` position that is not an error type is refused by E3
(CE2084). Then the argument is compared with the parameter of the instance (CE2006), and
its `nom` marker with the declared mode (CE2427, `record_conventions`). The call answers
the `Result` of the instance.

The receiver is `nom self` (`BuiltinMethod.nom_self`), and its convention is a fact of the
callee row (`receiver_convention`). The argument has the convention of its declared mode.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.builtin_signatures import BuiltinSignature
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arguments import (
    Parameters, compare_argument, marked_type, record_conventions, reject_misplaced_spread)
from sushi_lang.semantics.passes.types.calls.builtin_signature import (
    instance_return_type, settle_builtin_instance)
from sushi_lang.semantics.passes.types.method_registry import MethodFamily
from sushi_lang.semantics.param_modes import param_mode
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_signature_method(validator: TypeValidator, node: e.Call, receiver: Type,
                           name: str, family: MethodFamily,
                           signature: BuiltinSignature) -> Optional[Type]:
    """Check one call of a method with a signature row, give its argument its convention,
    and answer the `Result` of its instance (see the module docstring)."""
    callee_name = f"{display_type(receiver)}.{name}"
    if reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                               callee_name, family.arity):
        return None
    arg = node.args[0]
    if reject_misplaced_spread(validator, arg):
        return None
    written = visit.check_expr(validator, arg.value)
    if written is None:
        return None
    instance = settle_builtin_instance(validator, signature, receiver, [written], node.loc,
                                       name, unsolved_help)
    if instance is None:
        return None
    parameter = instance.params[0]
    compare_argument(validator, callee_name, arg, 1, marked_type(arg, written), parameter.ty,
                     mismatch_code=er.ERR.CE2006)
    record_conventions(validator, Parameters(node.shir_id, (param_mode(parameter),),
                                             [parameter.name]), [arg])
    return instance_return_type(validator, instance)


def unsolved_help(count: int) -> str:
    """The help of CE2063: the argument has no type that holds the type parameter."""
    pronoun = "them" if count > 1 else "it"
    return (f"no argument of this call has a type that holds {pronoun}; a method call has "
            f"no '@(...)' slot, so write the type parameter in a parameter type")
