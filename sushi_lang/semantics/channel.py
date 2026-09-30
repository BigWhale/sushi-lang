"""The error channel of a callable: one predicate for every kind (docs/design/error-channel.md).

A callable has a channel only when its signature writes `| E` or returns an explicit
`Result@(T, E)`. Every other callable is BARE: its body returns the value and a call
yields it. There is no default error type. The rule is the same for a free function,
an extension or perk method, a lambda and a function type, so every reader asks here.
"""
from __future__ import annotations

from typing import Any, Optional

from sushi_lang.semantics.typesys import Type


def spells_result(ty: Optional[Type]) -> bool:
    """Whether a written return type is a `Result@(T, E)`, as written or already interned."""
    if ty is None:
        return False
    from sushi_lang.semantics.generics.results import is_result_enum
    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(ty, GenericTypeRef):
        return ty.base_name == "Result"
    return is_result_enum(ty)


def declared_return(sig: Any) -> Optional[Type]:
    """The written return type of a declaration, a signature record or a function type."""
    for name in ("ret", "ret_type", "ok_type"):
        if hasattr(sig, name):
            return getattr(sig, name)
    return None


def has_channel(sig: Any) -> bool:
    """Whether the callable answers a Result: it writes `| E`, or it returns `Result@(T, E)`.

    A copy (a monomorphized instance, a lifted lambda) answers what its template WROTE:
    `fn id@(T)(nom T x) T` stays bare when `T` is a Result.
    """
    written = getattr(sig, "written_channel", None)
    if written is not None:
        return written
    if getattr(sig, "err_type", None) is not None:
        return True
    from sushi_lang.semantics.typesys import FunctionType
    if isinstance(sig, FunctionType):
        return False
    return spells_result(declared_return(sig))


def callable_text(name: str, kind: str) -> str:
    """How a diagnostic names a body: `function 'f'`, `method 'm'`, or `lambda`."""
    return "lambda" if kind == "lambda" else f"{kind} '{name}'"
