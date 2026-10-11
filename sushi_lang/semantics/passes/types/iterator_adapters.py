"""The adapters of `Iterator@(T)` in the typecheck pass (#1228, docs/design/iteration.md).

The arguments solve the method-level type parameter of an adapter through the one
solver of a built-in signature (`generics/builtin_signatures.py`). The RESULT is decided
here, because the item mode of the result follows the item mode of the receiver: `filter`
passes the mode through, `map` answers the function's owned result, `clone_each` owns
each item, and `enumerate` and `zip` keep the mode of each side in its tuple element.

One reader for both halves of the pass: inference calls `adapter_result` with
`report=False`, and validation calls it with `report=True` and stamps the answer.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.iterators import (
    enumerated, item_parts, lends_a_part, zipped)
from sushi_lang.semantics.generics.tuples import tuple_ref
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import IteratorType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import MethodCall
    from sushi_lang.semantics.generics.builtin_signatures import BuiltinInstance
    from sushi_lang.semantics.passes.types import TypeValidator

# The help of CE2129: the one escape is an owned copy of each item.
COLLECT_HELP = ("there is no `List@(peek T)`, and the compiler makes no copy: clone each "
                "item first, `.clone_each().{call}`")


def settle_adapter_instance(validator: 'TypeValidator', call: 'MethodCall',
                            instance: 'BuiltinInstance') -> 'BuiltinInstance':
    """The instance with the parameter of `zip` in the mode of its argument.

    `zip` keeps the item mode of each side, so its parameter takes an iterator of any
    item mode over the solved item: the argument's own type is the parameter's.
    """
    if call.method != "zip" or not call.args:
        return instance
    from sushi_lang.semantics.generics.pack_inference import infer_call_arg_type
    from sushi_lang.semantics.typesys import deref_type
    from .utils import settle_item_modes
    argument = settle_item_modes(validator, deref_type(infer_call_arg_type(validator,
                                                                           call.args[0])))
    expected = instance.params[0].ty
    if (isinstance(argument, IteratorType) and isinstance(expected, IteratorType)
            and argument.element_type == expected.element_type):
        return replace(instance, params=(replace(instance.params[0], ty=argument),))
    return instance


def adapter_result(validator: 'TypeValidator', call: 'MethodCall', receiver: Type,
                   instance: 'BuiltinInstance', *, report: bool) -> Optional[Type]:
    """What the adapter call yields, or None after a refusal (reported when `report`)."""
    if not isinstance(receiver, IteratorType):
        return None
    method = call.method
    if method == "map":
        return _iterator(validator, IteratorType(element_type=instance.substitution["U"]))
    if method == "filter":
        return receiver
    if method == "clone_each":
        if report and _refuses_a_clone(validator, call, receiver):
            return None
        return replace(receiver, peeks=())
    if method == "enumerate":
        return _tuple_item(validator, call, enumerated(receiver), receiver, report)
    if method == "zip":
        other = instance.params[0].ty
        if not isinstance(other, IteratorType):
            return None
        return _tuple_item(validator, call, zipped(receiver, other), receiver, report)
    if method == "fold":
        return instance.substitution["A"]
    if lends_a_part(receiver):
        if report:
            er.emit_with(validator.reporter, er.ERR.CE2129, call.loc, method=method,
                         type=display_type(receiver)) \
                .help(COLLECT_HELP.format(
                    call="collect()" if method == "collect" else f"{method}(...)")).emit()
        return None
    items = _intern(validator, GenericTypeRef("List", (receiver.element_type,)))
    if method == "collect" or items is None:
        return items
    return _intern(validator, tuple_ref((items, items)))


def _tuple_item(validator: 'TypeValidator', call: 'MethodCall',
                result: Optional[IteratorType], receiver: IteratorType,
                report: bool) -> Optional[Type]:
    """The result of `enumerate` or `zip`, or CE2419 when a `peek` would stand deeper
    than one tuple element: a side whose tuple item has parts of two modes."""
    if result is None:
        if report:
            er.emit(validator.reporter, er.ERR.CE2419, call.loc,
                    ty=display_type(receiver))
        return None
    return _iterator(validator, result)


def _iterator(validator: 'TypeValidator', iterator: IteratorType) -> Optional[Type]:
    """The iterator with its item interned, in its one form."""
    from .utils import settle_item_modes
    element = _intern(validator, iterator.element_type)
    if element is None:
        return None
    return settle_item_modes(validator, replace(iterator, element_type=element))


def _intern(validator: 'TypeValidator', ty: Optional[Type]) -> Optional[Type]:
    """The concrete type `ty` names, an instance the program has not built included."""
    from .utils import intern_declared_wrapper, intern_signature, resolve_declared_type
    if ty is None:
        return None
    intern_signature(validator, ty)
    interned = intern_declared_wrapper(validator, ty)
    return interned if interned is not None else resolve_declared_type(validator, ty)


def _refuses_a_clone(validator: 'TypeValidator', call: 'MethodCall',
                     receiver: IteratorType) -> bool:
    """`clone_each` clones each lent part, by the rule of `.clone()` on that part."""
    from .calls.methods import reject_uncloneable
    if not receiver.peeks:
        return False
    lent = (part for part, peek in zip(item_parts(receiver), receiver.peeks, strict=True)
            if peek)
    return any(reject_uncloneable(validator, call, part) for part in lent)
