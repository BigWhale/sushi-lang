"""Type resolution utilities for semantic analysis."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import (
    DynamicArrayType
)
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.internals.report import Span


def resolve_return_type_to_result(validator: 'TypeValidator',
                                   declared_type: 'Type',
                                   err_type_node: Optional['Type']) -> 'Type':
    """Convert a function's declared return type to its interned Result<T, E> enum."""
    resolved_type = declared_type

    if isinstance(declared_type, GenericTypeRef):
        if declared_type.base_name == "Result" and len(declared_type.type_args) == 2:
            resolved_type = resolve_unknown_type(
                declared_type,
                validator.struct_table.by_name,
                validator.enum_table.by_name
            )
        else:
            type_args_str = ", ".join(str(arg) for arg in declared_type.type_args)
            enum_name = f"{declared_type.base_name}<{type_args_str}>"
            if enum_name in validator.enum_table.by_name:
                resolved_type = validator.enum_table.by_name[enum_name]

    # Case 2/3: Implicit Result wrapping (T | E or just T).
    # An explicit Result<T, E> has already resolved to its interned enum above, and wrapping that
    # again would produce Result<Result<T, E>, StdError> -- hence the guard.
    from sushi_lang.semantics.generics.results import is_result_enum, ensure_result_type_in_table

    if not is_result_enum(resolved_type):

        if err_type_node:
            err_type = resolve_unknown_type(
                err_type_node,
                validator.struct_table.by_name,
                validator.enum_table.by_name
            )
        else:
            err_type = validator.enum_table.by_name.get("StdError")

        if err_type:
            interned = ensure_result_type_in_table(
                validator.enum_table, resolved_type, err_type,
                struct_table=validator.struct_table.by_name,
            )
            resolved_type = interned if interned is not None else resolved_type

    return resolved_type


def resolve_variable_type(validator: 'TypeValidator',
                          declared_type: 'Type',
                          type_span: 'Span') -> 'Type':
    """The concrete type a variable's ANNOTATION names, its wrapper interned.

    `resolve_declared_type` answers what the spelling names and `intern_declared_wrapper`
    builds the one kind a lookup cannot find -- `let Result@(T, E) r = mk()` compared
    unequal against the call it takes until the annotation interned too (#184).

    What is left is this position's OWN question: a `let` is where a HashMap key type is
    written, so CE2058 is read here and nowhere under it.
    """
    from .utils import intern_declared_wrapper, resolve_declared_type

    if isinstance(declared_type, GenericTypeRef):
        _reject_array_hashmap_key(validator, declared_type, type_span)

    interned = intern_declared_wrapper(validator, declared_type)
    if interned is not None:
        return interned

    resolved = resolve_declared_type(validator, declared_type)
    return resolved if resolved is not None else declared_type


def _reject_array_hashmap_key(validator: 'TypeValidator',
                              declared_type: GenericTypeRef,
                              type_span: 'Span') -> None:
    """A dynamic array cannot be a HashMap key: it has no equality (CE2058)."""
    if declared_type.base_name != "HashMap" or not declared_type.type_args:
        return

    key_type = declared_type.type_args[0]
    if isinstance(key_type, DynamicArrayType):
        er.emit(validator.reporter, er.ERR.CE2058, type_span,
                key_type=display_type(key_type))
