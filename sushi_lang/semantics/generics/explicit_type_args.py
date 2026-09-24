"""The type-argument count of a `@(...)` list: a call site, a written type, a target.

`identity@(i32, i32)(5)`, `let Box@(i32, i32) b` and `extend Box@(T, U)` are one fault:
the list does not give the generic the count it declares. ONE predicate answers the count
and ONE emitter writes CE2062 for every position (#796).
"""
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


def check_explicit_type_arg_arity(generic, n_args: int) -> Optional[str]:
    """The count a generic function or type expects, when `n_args` is not that count."""
    type_params = generic.type_params or []
    n_params = len(type_params)
    has_pack = bool(type_params) and type_params[-1].is_pack
    if has_pack:
        return None if n_args >= n_params - 1 else f"at least {n_params - 1}"
    return None if n_args == n_params else str(n_params)


def reject_type_arg_arity(reporter, name: str, generic, n_args: int, span,
                          filename: Optional[str] = None,
                          declared_at=None, declared_in: Optional[str] = None) -> bool:
    """CE2062 when a `@(...)` list does not give `generic` its count; True when refused.

    The note names the declaration, so the reader sees the count at its source.
    """
    from sushi_lang.internals import errors as er

    expected = check_explicit_type_arg_arity(generic, n_args)
    if expected is None:
        return False
    builder = er.emit_with(reporter, er.ERR.CE2062, span, filename=filename,
                           name=name, expected=expected, got=n_args)
    if declared_at is not None:
        builder.note(f"'{name}' is declared here", declared_at, declared_in)
    builder.emit()
    return True
