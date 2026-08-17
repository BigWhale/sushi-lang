"""Human-facing type rendering: the `@(...)` display form."""
from __future__ import annotations

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
)
from sushi_lang.semantics.generics.types import (
    GenericEnumType,
    GenericStructType,
    GenericTypeRef,
    TypePack,
)


def display_type(ty) -> str:
    """Render a type in the canonical `@(...)` surface form for diagnostics."""
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
        return f"{display_type(ty.base_type)}[{ty.size}]"

    if isinstance(ty, DynamicArrayType):
        return f"{display_type(ty.base_type)}[]"

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
        if str(ty.err_type) != "StdError":
            base += f" | {display_type(ty.err_type)}"
        return base

    if isinstance(ty, (GenericStructType, GenericEnumType)):
        params = ", ".join(str(tp) for tp in ty.type_params)
        return f"{ty.name}@({params})"

    if isinstance(ty, TypePack):
        return f"pack({', '.join(display_type(t) for t in ty.types)})"

    if isinstance(ty, UnknownType):
        return display_type_name(ty.name)

    return str(ty)


def display_type_name(name: str) -> str:
    """Best-effort `@(...)` for a bare identity name lacking structured metadata."""
    if "<" not in name:
        return name
    if "->" in name:
        return name
    if name.count("<") != name.count(">"):
        return name
    return name.replace("<", "@(").replace(">", ")")
