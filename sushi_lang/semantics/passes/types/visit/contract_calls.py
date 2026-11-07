"""The contract calls of the typecheck pass over SHIR: `x.hash()`, `x.eq(y)`, `x.compare(y)`
and `x.to_str()` (docs/design/ir.md 7.6, 8.6).

The arm of the families `derived_hash`, `array_hash` and `contract`. A call takes the
arguments of its contract, each one a borrow at the type of the receiver, and yields the type
of its contract. An opaque type parameter has the family of a contract that a constraint of
it promises.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.contracts import (
    CONTRACT_METHOD, CONTRACT_METHOD_ARITY, CONTRACT_METHOD_RETURN)
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.generics.types import TypeParameter
from sushi_lang.semantics.method_effects import CONTRACT_FAMILY, DERIVED_HASH_FAMILY
from sushi_lang.semantics.param_modes import ParamMode
from sushi_lang.semantics.passes.types.arguments import Parameters, check_arguments
from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY, MethodFamily
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import BuiltinType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

_HASH = "hash"
_HASHABLE = "Hashable"


def promised_contract_family(validator: TypeValidator, receiver: Type,
                             name: str) -> Optional[MethodFamily]:
    """The family of the contract method `name` that a constraint of the opaque `receiver`
    promises, or None. A method of any other perk is no contract call."""
    if not (isinstance(receiver, TypeParameter) and receiver.is_opaque):
        return None
    perk = validator.perk_impl_table.providing_perk(receiver, name)
    if perk == _HASHABLE and name == _HASH:
        return METHOD_TYPE_REGISTRY.family(DERIVED_HASH_FAMILY)
    if perk is not None and CONTRACT_METHOD.get(perk) == name:
        return METHOD_TYPE_REGISTRY.family(CONTRACT_FAMILY)
    return None


def _parameter_names(validator: TypeValidator, receiver: Type, name: str,
                     count: int) -> list[str]:
    """The names of the parameters of a contract call, for a diagnostic: the names that the
    contract declares when a constraint promises it, else a position."""
    promised = validator.perk_impl_table.get_method(receiver, name)
    if isinstance(receiver, TypeParameter) and promised is not None:
        return [param.name for param in promised.params]
    return [f"#{index}" for index in range(1, count + 1)]


def check_contract_method(validator: TypeValidator, node: e.Call, receiver: Type, name: str,
                          family: MethodFamily) -> Type:
    """A contract call yields the type of its contract, also after a fault of an argument.
    A wrong count is CE2009 and skips the argument types."""
    shown = f"{display_type(receiver)}.{name}"
    if name == _HASH:
        reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args), shown,
                                family.arity)
        return BuiltinType.U64
    if not reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args), shown,
                                   family.arity):
        count = CONTRACT_METHOD_ARITY[name]
        declared = Parameters(node.shir_id, (ParamMode.BORROW,) * count,
                              _parameter_names(validator, receiver, name, count))
        check_arguments(validator, shown, [receiver] * count, node.args, node.loc,
                        mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                        declared=declared)
    return CONTRACT_METHOD_RETURN[name]
