"""Type resolution utilities for semantic analysis."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import (
    BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType,
    UnknownType
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
    """Resolve variable type from declaration."""
    if isinstance(declared_type, (BuiltinType, StructType, EnumType)):
        return declared_type

    from sushi_lang.semantics.typesys import FunctionType

    # Types that CONTAIN another type: resolve the MEMBERS, not the wrapper. `let P[] arr`
    # parses as DynamicArrayType(UnknownType("P")), and leaving that put an UnknownType in
    # the variable table -- every later compare failed, and since both spell themselves "P"
    # the message read `expected P, got P` (#284). Delegated to `resolve_declared_type`, the
    # ONE answer to what a declared spelling names.
    if isinstance(declared_type, (ArrayType, DynamicArrayType, FunctionType)):
        from .utils import resolve_declared_type
        return resolve_declared_type(validator, declared_type)

    if isinstance(declared_type, UnknownType):
        resolved = resolve_unknown_type(
            declared_type,
            validator.struct_table.by_name,
            validator.enum_table.by_name
        )
        return resolved

    if isinstance(declared_type, GenericTypeRef):
        # Result<T, E> interns to an EnumType, exactly like Maybe<T>. It used to resolve to a
        # ResultType here, which is not an EnumType -- so `let Result<T, E> r = mk()` compared
        # the annotation against the call's type and found them unequal (#184).
        if declared_type.base_name == "Result" and len(declared_type.type_args) == 2:
            from sushi_lang.semantics.generics.results import ensure_result_type_in_table
            interned = ensure_result_type_in_table(
                validator.enum_table,
                declared_type.type_args[0],
                declared_type.type_args[1],
                struct_table=validator.struct_table.by_name,
            )
            if interned is not None:
                return interned

        if declared_type.base_name == "HashMap" and len(declared_type.type_args) >= 1:
            key_type = declared_type.type_args[0]
            if isinstance(key_type, DynamicArrayType):
                er.emit(validator.reporter, er.ERR.CE2058, type_span, key_type=display_type(key_type))

        type_args_str = ", ".join(str(arg) for arg in declared_type.type_args)
        concrete_name = f"{declared_type.base_name}<{type_args_str}>"

        if concrete_name in validator.enum_table.by_name:
            return validator.enum_table.by_name[concrete_name]

        if concrete_name in validator.struct_table.by_name:
            return validator.struct_table.by_name[concrete_name]

    return declared_type
