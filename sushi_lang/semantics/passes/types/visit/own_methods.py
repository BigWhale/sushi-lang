"""The arm of the `Own@(T)` family in the typecheck pass over SHIR (docs/design/ir.md 7.3,
8.3).

A method of `Own@(T)` answers the type of its row (`OwnMethodInferrer`, the one answer of
the arm and of the inference of the early passes): `get` answers `T`, `clone` and `alloc`
the `Own@(T)` itself, and `destroy` answers `~`. The questions come in the order of `main`:
a `clone` of an `Own` whose payload holds a resource is CE2431 (CE4018 for an opaque type
parameter), a wrong count is CE2009, and only then is the argument of `alloc` read, at the
type `T` (CE2006). The call answers the type of its row after each fault.

The convention of each argument is the answer of `argument_conventions`: `alloc` stores its
argument into the new allocation, a slot that takes it by position with no marker to
compare (`Owned`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.own import OWN_METHOD_ARITY, own_payload_type
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.method_effects import OWN_FAMILY, argument_conventions
from sushi_lang.semantics.passes.types.arguments import (
    check_argument, record_builtin_conventions)
from sushi_lang.semantics.passes.types.calls.methods import reject_uncloneable
from sushi_lang.semantics.passes.types.method_registry import MethodFamily, OwnMethodInferrer
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import StructType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_own_method(validator: TypeValidator, node: e.Call, receiver: Type, name: str,
                     family: MethodFamily) -> Optional[Type]:
    """The arm of the `own` family: check one call against its row, give each argument its
    convention, and answer the type of the row (see the module docstring)."""
    if name not in OWN_METHOD_ARITY:
        raise_internal_error("CE0080", method=name)
    payload = own_payload_type(receiver) if isinstance(receiver, StructType) else None
    if payload is None:
        raise_internal_error("CE0000", span=node.loc,
                             detail="the own family answered a receiver that is no Own")
    answer = OwnMethodInferrer(receiver, name, validator).infer_return_type()
    if name == "clone" and reject_uncloneable(validator, node, receiver):
        return answer
    if reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                               f"{display_type(receiver)}.{name}", family.arity):
        return answer
    if name == "alloc":
        check_argument(validator, name, node.args[0], 1, payload,
                       mismatch_code=er.ERR.CE2006)
    record_builtin_conventions(validator, node.shir_id, node.args,
                               argument_conventions(OWN_FAMILY, name, len(node.args)))
    return answer
