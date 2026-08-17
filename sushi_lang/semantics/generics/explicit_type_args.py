"""Explicit call-site type arguments: `identity@(i32)(5)`."""
from __future__ import annotations
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type


def _as_name_dict(table):
    """Normalize a struct/enum table to its `{name: Type}` dict."""
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
    """Validate the count of explicit type args against the function's type params."""
    type_params = generic_func.type_params or []
    n_params = len(type_params)
    has_pack = bool(type_params) and getattr(type_params[-1], "is_pack", False)
    if has_pack:
        return None if n_args >= n_params - 1 else f"at least {n_params - 1}"
    return None if n_args == n_params else str(n_params)
