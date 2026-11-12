"""The spelling of a type that the source writes before a dot, with its alias.

SHIR lowering names a construction and a member of a type by this spelling, and the
typecheck pass reads the same spelling back (docs/design/ir.md 7.4).
"""
from __future__ import annotations

from typing import TypeAlias

from sushi_lang.semantics.typesys import BuiltinType, UnknownType

#: A type as written before a dot: a type name, or a type behind an alias.
WrittenType: TypeAlias = BuiltinType | UnknownType


def written_name(ty: WrittenType) -> str:
    """The name of a type as the source writes it, with its alias (`geo.Sign`)."""
    namespace = ty.namespace if isinstance(ty, UnknownType) else None
    return str(ty) if namespace is None else f"{namespace}.{ty}"
