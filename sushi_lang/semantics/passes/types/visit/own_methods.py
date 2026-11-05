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
from sushi_lang.semantics.generics.own import own_payload_type
from sushi_lang.semantics.passes.types.arguments import check_argument
from sushi_lang.semantics.passes.types.method_registry import MethodFamily, OwnMethodInferrer
from sushi_lang.semantics.passes.types.visit.container_methods import (
    ContainerArm, check_container_method)
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_own_method(validator: TypeValidator, node: e.Call, receiver: Type, name: str,
                     family: MethodFamily) -> Optional[Type]:
    """The arm of the `own` family: check one call against its row, give each argument its
    convention, and answer the type of the row (see the module docstring)."""
    def check_arguments(payload: Type) -> None:
        if name == "alloc":
            check_argument(validator, name, node.args[0], 1, payload,
                           mismatch_code=er.ERR.CE2006)

    return check_container_method(validator, node, receiver, name, family, _OWN,
                                  check_arguments)


_OWN = ContainerArm("CE0080", "Own", OwnMethodInferrer,
                    lambda receiver, _validator: own_payload_type(receiver))
