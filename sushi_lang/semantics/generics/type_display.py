"""Human-facing type rendering: the `@(...)` display form.

Types are stored and interned under their `<...>` identity name (`StructType.name
== "List<i32>"`), which name-mangling, the ~60 `startswith("List<")` match sites,
and the `type_strings` name->Type parser all depend on. Those `__str__`/identity
strings MUST stay `<...>`.

But the *language* now spells generics `List@(i32)`, so showing `List<i32>` in a
diagnostic (or `.debug()` header) displays a syntax that no longer exists. This
module is the separate DISPLAY layer: `display_type(ty)` renders the `@(...)` form
for user-facing messages, reconstructing from the structured `generic_base` /
`generic_args` metadata rather than the identity string. It never feeds identity,
mangling, or table keys.

Invariant: for any type that carries no generic `<...>`, `display_type(ty) == str(ty)`,
so routing a non-generic site through it is always safe.

`display_type_name(name)` is the string-level counterpart, for the few diagnostic
sites that hold an already-interned name and no `Type` object.

`tests/unit/test_diagnostics_use_display_type.py` is the CI gate: it fails if a
diagnostic interpolates a type without going through one of these two.
"""
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
        # A `nom` parameter is part of the type, so it must be visible in a diagnostic;
        # `peek`/`poke` already render from the ReferenceType arm above.
        params = ", ".join(
            f"{m.marker} {display_type(p)}" if m.marker and not m.by_pointer
            else display_type(p)
            for p, m in zip(ty.param_types, ty.modes, strict=True)
        )
        base = f"fn({params}) -> {display_type(ty.ok_type)}"
        # Hide the implicit StdError, matching FunctionType.__str__.
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

    # BuiltinType, TypeParameter, ForeignPtrType, BorrowMode, and anything else
    # carry no generic brackets -- their str() is already the display form.
    return str(ty)


def display_type_name(name: str) -> str:
    """Best-effort `@(...)` for a bare identity name lacking structured metadata.

    The string-level counterpart to `display_type`, for the diagnostic sites that
    only ever hold an already-interned *name* (`CE0122`'s recursion chain, the
    duplicate-function and perk-target codes) and have no `Type` object to render.

    Also covers the narrow gap where `monomorphize/transformer.py` rebuilds a
    Struct/Enum dropping `generic_base`/`generic_args`, or a `<...>`-bearing
    `UnknownType`. A well-formed identity name has balanced brackets and no `->`,
    so a straight bracket swap is correct (and nests: `Result<List<i32>>` ->
    `Result@(List@(i32))`). Anything else is left untouched rather than risk
    corrupting it.
    """
    if "<" not in name:
        return name
    if "->" in name:
        return name
    if name.count("<") != name.count(">"):
        return name
    return name.replace("<", "@(").replace(">", ")")
