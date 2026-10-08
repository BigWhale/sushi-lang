"""Human-facing type rendering: the `@(...)` display form."""
from __future__ import annotations

import re

from sushi_lang.semantics.typesys import (
    ArrayType,
    DynamicArrayType,
    EnumType,
    FunctionType,
    IteratorType,
    PointerType,
    ReferenceType,
    StructType,
    UnknownType,
    element_text,
)
from sushi_lang.semantics.generics.types import (
    OPAQUE_MARK,
    GenericEnumType,
    GenericStructType,
    GenericTypeRef,
    TypePack,
    TypeParameter,
)

# The template part of an opaque parameter in an interned name: `#main.f` in `T#main.f`.
_OPAQUE_OWNER = re.compile(re.escape(OPAQUE_MARK) + r"[^\s,<>()\[\]']+")


def display_type(ty) -> str:
    """Render a type in the canonical `@(...)` surface form for diagnostics."""
    from sushi_lang.semantics.generics.tuples import (
        display_tuple, is_tuple_type, tuple_elements)
    if is_tuple_type(ty) and not isinstance(ty, ReferenceType):
        return display_tuple(display_type(element) for element in tuple_elements(ty))

    if isinstance(ty, (StructType, EnumType)):
        if ty.generic_base is not None and ty.generic_args is not None:
            args = ", ".join(display_type(a) for a in ty.generic_args)
            return f"{ty.generic_base}@({args})"
        return display_type_name(ty.name)

    if isinstance(ty, GenericTypeRef):
        args = ", ".join(display_type(a) for a in ty.type_args)
        return f"{ty.base_name}@({args})"

    if isinstance(ty, IteratorType):
        return f"Iterator@({display_type(ty.element_type)})"

    if isinstance(ty, ArrayType):
        return f"{element_text(ty.base_type, display_type)}[{ty.size}]"

    if isinstance(ty, DynamicArrayType):
        return f"{element_text(ty.base_type, display_type)}[]"

    if isinstance(ty, ReferenceType):
        return f"{ty.mutability} {display_type(ty.referenced_type)}"

    if isinstance(ty, PointerType):
        return f"{display_type(ty.pointee_type)}*"

    if isinstance(ty, FunctionType):
        params = ", ".join(
            f"{m.marker} {display_type(p)}" if m.marker and not m.by_pointer
            else display_type(p)
            for p, m in zip(ty.param_types, ty.modes, strict=True)
        )
        base = f"fn({params}) -> {display_type(ty.ok_type)}"
        if ty.err_type is not None:
            base += f" | {display_type(ty.err_type)}"
        return base

    if isinstance(ty, (GenericStructType, GenericEnumType)):
        params = ", ".join(str(tp) for tp in ty.type_params)
        return f"{ty.name}@({params})"

    if isinstance(ty, TypePack):
        return f"pack({', '.join(display_type(t) for t in ty.types)})"

    if isinstance(ty, UnknownType):
        return display_type_name(ty.name)

    # A user writes `T`; the owner of an opaque parameter is an internal identity.
    if isinstance(ty, TypeParameter):
        return ty.name

    return str(ty)


def display_type_name(name: str) -> str:
    """Best-effort `@(...)` for a bare identity name lacking structured metadata."""
    from sushi_lang.semantics.generics.tuples import TUPLE_BASE
    if OPAQUE_MARK in name:
        name = _OPAQUE_OWNER.sub("", name)
    if TUPLE_BASE in name:
        return _display_tuples_in_name(name)
    if "<" not in name:
        return name
    if "->" in name:
        return name
    if name.count("<") != name.count(">"):
        return name
    return name.replace("<", "@(").replace(">", ")")



_INNERMOST_TUPLE = re.compile(r"\$Tuple<([^<>]*)>")


def _display_tuples_in_name(name: str) -> str:
    """A bare identity name that spells a tuple: `$Tuple<i32, string>` reads `(i32, string)`.

    The innermost tuple is rewritten first, so a tuple nested in a tuple, in a generic
    argument or in a function type reads as written.
    """
    while True:
        rewritten = _INNERMOST_TUPLE.sub(r"(\1)", name)
        if rewritten == name:
            break
        name = rewritten
    return display_type_name(name)
