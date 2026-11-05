"""The arm of the `List@(T)` family in the typecheck pass over SHIR (docs/design/ir.md 7.3,
8.3).

A method of `List@(T)` answers the type of its row (`ListMethodInferrer`, the one answer of
the arm and of the inference of the early passes). The questions come in the order of
`main`: a `clone` of a List that holds a resource is CE2431 (CE4018 for an opaque type
parameter), a wrong count is CE2009, and only then are the arguments read. A search reads
its needle first (CE2100, CE2006), and an element and an index come after it: an index or
a count is an i32 (CE2006), and an element has the type `T` (CE2006). The call answers the
type of its row after each fault.

The convention of each argument is the answer of `argument_conventions`: the element of
`push` and of `insert` is a slot, which takes its value by position with no marker to
compare (`Owned`). Each other argument borrows (`Guaranteed`), the index of `insert` too,
and a `nom` on it is CE2427.
"""
from __future__ import annotations

from enum import Enum, auto
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.list import parse_list_types
from sushi_lang.semantics.passes.types.arguments import (
    argument_span, check_argument, marked_type)
from sushi_lang.semantics.passes.types.arrays import (
    check_i32_operand, check_search_needle, reject_non_i32)
from sushi_lang.semantics.passes.types.method_registry import ListMethodInferrer, MethodFamily
from sushi_lang.semantics.passes.types.visit.container_methods import (
    ContainerArm, check_container_method)
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


class _Argument(Enum):
    """What one argument of a method is."""

    INDEX = auto()    # an index or a count, an i32 (#870)
    ELEMENT = auto()  # a value of the element type, typed by its position
    NEEDLE = auto()   # the needle of a search, through the one rule of the searches


#: The arguments of each method that takes one, in order. A method that is not here takes
#: no argument. `with_capacity` called on a value takes the count of the static.
_ARGUMENTS: Mapping[str, tuple[_Argument, ...]] = MappingProxyType({
    "push": (_Argument.ELEMENT,),
    "insert": (_Argument.INDEX, _Argument.ELEMENT),
    "get": (_Argument.INDEX,),
    "remove": (_Argument.INDEX,),
    "reserve": (_Argument.INDEX,),
    "with_capacity": (_Argument.INDEX,),
    "contains": (_Argument.NEEDLE,),
    "index_of": (_Argument.NEEDLE,),
    "index_of_from": (_Argument.NEEDLE, _Argument.INDEX),
})


def _check_arguments(validator: TypeValidator, node: e.Call, element: Type,
                     name: str) -> None:
    """Each argument by its kind. An element with no equality refuses a search, and the
    arguments after the needle are not read then."""
    for position, kind in enumerate(_ARGUMENTS.get(name, ())):
        arg = node.args[position]
        if kind is _Argument.NEEDLE:
            if check_search_needle(validator, node, element):
                return
        elif kind is _Argument.INDEX:
            got = marked_type(arg, check_i32_operand(validator, arg.value))
            reject_non_i32(validator, arg.value, got, argument=position + 1,
                           span=argument_span(arg))
        else:
            check_argument(validator, name, arg, position + 1, element,
                           mismatch_code=er.ERR.CE2006)


def check_list_method(validator: TypeValidator, node: e.Call, receiver: Type, name: str,
                      family: MethodFamily) -> Optional[Type]:
    """The arm of the `list` family: check one call against its row, give each argument
    its convention, and answer the type of the row (see the module docstring)."""
    return check_container_method(
        validator, node, receiver, name, family, _LIST,
        lambda element: _check_arguments(validator, node, element, name))


_LIST = ContainerArm("CE0083", "List", ListMethodInferrer, parse_list_types)
