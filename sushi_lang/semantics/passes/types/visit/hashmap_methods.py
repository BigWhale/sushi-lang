"""The arm of the `HashMap@(K, V)` family in the typecheck pass over SHIR (docs/design/ir.md
7.3, 8.3).

A method of `HashMap@(K, V)` answers the type of its row (`HashMapMethodInferrer`, the one
answer of the arm and of the inference of the early passes). The questions come in the
order of `main`: a `clone` of a map that holds a resource is CE2431 (CE4018 for an opaque
type parameter), a wrong count is CE2009, and only then are the arguments read. A key has
the type `K` and a value the type `V` (CE2006). The call answers the type of its row after
each fault.

The convention of each argument is the answer of `argument_conventions`: `insert` stores
its key and its value into a slot, which takes each by position with no marker to compare
(`Owned`). The key of a lookup borrows (`Guaranteed`), and a `nom` on it is CE2427. The key
rules of the map are a rule of its WRITTEN type (`reject_unusable_key`), never of a call.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.hashmap import parse_hashmap_types
from sushi_lang.semantics.passes.types.arguments import check_argument
from sushi_lang.semantics.passes.types.method_registry import (
    HashMapMethodInferrer, MethodFamily)
from sushi_lang.semantics.passes.types.visit.container_methods import (
    ContainerArm, check_container_method)
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import StructType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


#: The methods whose one argument is a key. `insert` takes a key, then a value.
_KEY_METHODS = frozenset({"get", "contains_key", "remove"})


def check_hashmap_method(validator: TypeValidator, node: e.Call, receiver: Type, name: str,
                         family: MethodFamily) -> Optional[Type]:
    """The arm of the `hashmap` family: check one call against its row, give each argument
    its convention, and answer the type of the row (see the module docstring)."""
    def check_arguments(types: tuple[Type, Type]) -> None:
        key, value = types
        expected = (key, value) if name == "insert" else (key,) if name in _KEY_METHODS else ()
        for position, ty in enumerate(expected):
            check_argument(validator, name, node.args[position], position + 1, ty,
                           mismatch_code=er.ERR.CE2006)

    return check_container_method(validator, node, receiver, name, family, _HASHMAP,
                                  check_arguments)


def _key_and_value(receiver: StructType,
                   validator: TypeValidator) -> Optional[tuple[Type, Type]]:
    key, value = parse_hashmap_types(receiver, validator)
    return None if key is None or value is None else (key, value)


_HASHMAP = ContainerArm(er.ERR.CE0085.code, "HashMap", HashMapMethodInferrer, _key_and_value)
