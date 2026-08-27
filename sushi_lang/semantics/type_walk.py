"""One walk over a type, so every predicate over types sees the same shape.

Three predicates used to recurse independently and each had a different hole:
`validate_type_name` fell through a `ReferenceType`, so `peek Nope` reached the backend as
a CE0020 "compiler bug"; `contains_foreign_ptr` fell through a `FunctionType`, so a
`fn(ptr) i32` in a `public fn` signature was invisible to CE5008. A predicate is now one
line over this generator, and `tests/unit/test_type_walk_is_total.py` is the gate.

The walk yields the type it is given before anything it holds, so a predicate that asks
about the type itself needs no special case.
"""
from __future__ import annotations

from typing import Iterator, Optional, Set

from sushi_lang.semantics.typesys import (
    ArrayType,
    DynamicArrayType,
    EnumType,
    FunctionType,
    IteratorType,
    PointerType,
    ReferenceType,
    StructType,
    Type,
    UnknownType,
)


# A kind with nothing inside it. Named rather than implied, so the gate can tell a
# deliberate leaf from a forgotten arm.
TERMINAL_KINDS = frozenset({"BuiltinType", "ForeignPtrType", "TypeParameter"})


# A kind that DECLARES a name, as opposed to one that merely spells it. The cycle guard
# keys on these alone: an `UnknownType("Node")` also carries the name, and letting it
# consume the name would stop the walk before the declaration it resolves to is entered.
_DECLARATION_KINDS = frozenset(
    {"StructType", "EnumType", "GenericStructType", "GenericEnumType"}
)


def _nominal_name(ty: Type) -> Optional[str]:
    """The name a type is identified BY, when it declares one.

    Type identity is nominal (`docs/design/type-identity.md`), so a name is what the cycle
    guard keys on -- two spellings of one name are one type.
    """
    if type(ty).__name__ not in _DECLARATION_KINDS:
        return None
    name = getattr(ty, "name", None)
    return name if isinstance(name, str) else None


def walk_named_types(
    ty: Optional[Type],
    structs: Optional[dict] = None,
    enums: Optional[dict] = None,
    _visited: Optional[Set[str]] = None,
) -> Iterator[Type]:
    """Every type reachable from `ty`, `ty` itself first.

    `structs` and `enums` let a bare `UnknownType` name resolve to its declaration and be
    walked through. Without them a bare name is a leaf: a caller that has no tables gets
    the name, not a crash.
    """
    if ty is None:
        return
    if _visited is None:
        _visited = set()

    # A declaration already entered is not entered again, and is not yielded again
    # either: one name is one type, so a second arrival carries nothing new.
    name = _nominal_name(ty)
    if name is not None:
        if name in _visited:
            return
        _visited.add(name)

    yield ty

    def below(inner: Optional[Type]) -> Iterator[Type]:
        yield from walk_named_types(inner, structs, enums, _visited)

    if isinstance(ty, (ArrayType, DynamicArrayType)):
        yield from below(ty.base_type)
    elif isinstance(ty, ReferenceType):
        yield from below(ty.referenced_type)
    elif isinstance(ty, PointerType):
        yield from below(ty.pointee_type)
    elif isinstance(ty, IteratorType):
        yield from below(ty.element_type)
    elif isinstance(ty, FunctionType):
        for param in ty.param_types or ():
            yield from below(param)
        yield from below(ty.ok_type)
        yield from below(ty.err_type)
    elif isinstance(ty, StructType):
        for _field_name, field_type in ty.fields:
            yield from below(field_type)
    elif isinstance(ty, EnumType):
        for variant in ty.variants:
            for associated in variant.associated_types:
                yield from below(associated)
    elif isinstance(ty, UnknownType):
        resolved = None
        if structs and ty.name in structs:
            resolved = structs[ty.name]
        elif enums and ty.name in enums:
            resolved = enums[ty.name]
        if resolved is not None:
            yield from below(resolved)
    else:
        yield from _walk_generic(ty, below)


def _walk_generic(ty: Type, below) -> Iterator[Type]:
    """The kinds that live in `semantics/generics`, kept out of the main dispatch.

    A generic TEMPLATE and a type PACK are not in the `Type` union, but both hold types and
    both sit in the symbol tables, so a walk that skipped them would let a private type
    hide in a template's field.
    """
    from sushi_lang.semantics.generics.types import (
        GenericEnumType,
        GenericStructType,
        GenericTypeRef,
        TypePack,
    )

    if isinstance(ty, GenericTypeRef):
        for arg in ty.type_args or ():
            yield from below(arg)
    elif isinstance(ty, TypePack):
        for member in ty.types or ():
            yield from below(member)
    elif isinstance(ty, GenericStructType):
        for _field_name, field_type in ty.fields:
            yield from below(field_type)
    elif isinstance(ty, GenericEnumType):
        for variant in ty.variants:
            for associated in variant.associated_types:
                yield from below(associated)
