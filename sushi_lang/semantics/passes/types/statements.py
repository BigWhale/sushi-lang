"""Statement validation for type validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import EnumType
from sushi_lang.semantics.param_modes import ParamMode, receiver_mode

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from . import TypeValidator


def reject_unspelled_result(validator: 'TypeValidator', value) -> None:
    """CE2030: a `return` in a channel body spells no `Result` constructor.

    Two callers, one decision (`spells_a_result`): the `Return` arm of a body as it is
    walked, and a template body as it is written (#1070).
    """
    er.emit_with(validator.reporter, er.ERR.CE2030, value.loc) \
        .help("wrap return value: return Result.Ok(value)").emit()


def _maybe_some_payload(ty):
    """The Some payload of a `Maybe@(T)`, or None when this is not one."""
    if not isinstance(ty, EnumType) or not is_instance_of(ty, "Maybe"):
        return None
    some_variant = ty.get_variant("Some")
    if some_variant is None or len(some_variant.associated_types) != 1:
        return None
    return some_variant.associated_types[0]


def protocol_item_type(validator: 'TypeValidator', iterable_type) -> Optional[Type]:
    """The T of a nullary `next()` answering `Maybe@(T)` on this type, or None.

    The one rule of the protocol, for a `foreach` and for `Iterator.new(nom x)` alike.
    It reports nothing and stamps nothing.
    """
    from sushi_lang.semantics.passes.types.calls.methods import (
        RESOLUTION_REPORTED, extension_call_result_type, resolve_method)

    resolved = resolve_method(validator, iterable_type, "next", report=False)
    if resolved is None or resolved is RESOLUTION_REPORTED:
        return None
    if getattr(resolved.method, "params", None):
        return None
    # A CONSUMING receiver cannot answer twice: the first call would spend the loop's
    # own iterator and the second would read a value that has been given away. So
    # `nom self` on a `next()` makes a type unwalkable rather than walkable once.
    if receiver_mode(getattr(resolved.method, "self_mode", None)) is ParamMode.NOM:
        return None
    return _maybe_some_payload(extension_call_result_type(validator, resolved.method))
