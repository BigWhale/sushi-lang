"""Type resolution utilities for semantic analysis."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.semantics.type_resolution import resolve_unknown_type

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.internals.report import Span


def channel_result(validator: 'TypeValidator', declared_type: Optional['Type'],
                   err_type_node: Optional['Type']) -> Optional['Type']:
    """The interned `Result@(T, E)` a body answers, or None when the body is BARE.

    ONE derivation for a function, a method and a lifted lambda: the channel is the
    spelled `| E`, or the explicit `Result@(T, E)` return; with neither there is no
    channel and no default error type (docs/design/error-channel.md).
    """
    from sushi_lang.semantics.generics.results import (
        ensure_result_type_in_table, is_result_enum, signature_result_arms)

    if is_result_enum(declared_type):
        return declared_type
    arms = signature_result_arms(declared_type, err_type_node)
    if arms is None:
        return None
    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    ok = resolve_unknown_type(arms[0], structs, enums)
    err = resolve_unknown_type(arms[1], structs, enums)
    return ensure_result_type_in_table(validator.enum_table, ok, err, struct_table=structs)


def call_yield(validator: 'TypeValidator', ret: Optional['Type'],
               err_type: Optional['Type']) -> Optional['Type']:
    """What a call yields: the callee's channel Result, or its bare return value.

    ONE answer for every callee kind -- a function, a function value, an extension or
    perk method -- so `??`, `.realise` and the chain gate (CE2515) read one rule. A
    written wrapper return (`Maybe@(T)`) is interned, as a declared type is.
    """
    if ret is None:
        return None
    channel = channel_result(validator, ret, err_type)
    if channel is not None:
        return channel
    from .utils import intern_declared_wrapper, resolve_declared_type
    interned = intern_declared_wrapper(validator, ret)
    return interned if interned is not None else resolve_declared_type(validator, ret)


def resolve_variable_type(validator: 'TypeValidator',
                          declared_type: 'Type',
                          type_span: 'Span') -> 'Type':
    """The concrete type a variable's ANNOTATION names, its wrapper interned.

    `resolve_declared_type` answers what the spelling names and `intern_declared_wrapper`
    builds the one kind a lookup cannot find -- `let Result@(T, E) r = mk()` compared
    unequal against the call it takes until the annotation interned too (#184).
    """
    from .utils import intern_declared_wrapper, resolve_declared_type

    interned = intern_declared_wrapper(validator, declared_type)
    if interned is not None:
        return interned

    resolved = resolve_declared_type(validator, declared_type)
    return resolved if resolved is not None else declared_type
