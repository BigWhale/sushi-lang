"""Explicit call-site type arguments: `identity@(i32)(5)`.

When a generic call carries an explicit `@(...)` type-argument list, it overrides
inference. Both the Pass 1.5 instantiation collector and the Pass 2 validator route
through the two helpers here so they agree on the concrete type-arg tuple (and thus
the mangled symbol). Resolution reuses `resolve_unknown_type`, so a bare primitive
passes through, a `UnknownType` name resolves to its Struct/Enum, and a nested
`GenericTypeRef` (`List@(i32)`) resolves to its concrete monomorphized type.
"""
from __future__ import annotations
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type


def _as_name_dict(table):
    """Normalize a struct/enum table to its `{name: Type}` dict.

    Pass 2 hands us plain dicts; the Pass 1.5 collector hands us StructTable /
    EnumTable wrappers whose dict lives on `.by_name`. `resolve_unknown_type` does
    `name in table`, which a wrapper without `__contains__` cannot satisfy.
    """
    if table is None:
        return {}
    return getattr(table, "by_name", table)


def resolve_explicit_type_args(type_args, struct_table, enum_table) -> Tuple["Type", ...]:
    """Resolve the raw parsed call-site type args to concrete types."""
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    structs = _as_name_dict(struct_table)
    enums = _as_name_dict(enum_table)
    return tuple(
        resolve_unknown_type(t, structs, enums) for t in type_args
    )


def check_explicit_type_arg_arity(generic_func, n_args: int) -> Optional[str]:
    """Validate the count of explicit type args against the function's type params.

    Returns None when the count is valid, otherwise a human-readable string
    describing the expected count (for the CE2062 message). Explicit type args are
    all-or-nothing: the grammar has no wildcard, so a partial list is impossible.

    Non-pack: the count must match exactly. Pack (the last type-param is a
    ``...Ts``): at least ``len(type_params) - 1`` (the leading params, with the
    pack absorbing zero or more). This mirrors the monomorphizer's
    ``build_substitution`` invariant, keeping its internal ValueError unreachable
    from source.
    """
    type_params = generic_func.type_params or []
    n_params = len(type_params)
    has_pack = bool(type_params) and getattr(type_params[-1], "is_pack", False)
    if has_pack:
        return None if n_args >= n_params - 1 else f"at least {n_params - 1}"
    return None if n_args == n_params else str(n_params)
