"""One walk over a type, so every predicate over types sees the same shape.

Three predicates used to recurse independently and each had a different hole:
`validate_type_name` fell through a `ReferenceType`, so `peek Nope` reached the backend as
a CE0020 "compiler bug"; `contains_foreign_ptr` fell through a `FunctionType`, so a
`fn(ptr) i32` in a `public fn` signature was invisible to CE5008. A predicate is now one
line over this generator, and `tests/unit/test_type_walk_is_total.py` is the gate.

The walk yields the type it is given before anything it holds, so a predicate that asks
about the type itself needs no special case. `map_named_types` is the other direction over
the SAME arm table: it REBUILDS what the walk yields, so a resolver can neither enter a
kind the walk misses nor miss a kind the walk enters (#718).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Iterator, Optional, Set

from sushi_lang.semantics.typesys import Type, UnknownType


# A kind with nothing inside it. Named rather than implied, so the gate can tell a
# deliberate leaf from a forgotten arm.
TERMINAL_KINDS = frozenset({"BuiltinType", "ForeignPtrType", "TypeParameter"})


# The ARM TABLE: each composite kind, and the attributes that carry the types it holds.
# An attribute holds one type or a tuple of them. The walk reads the table to descend and
# `map_named_types` reads it to rebuild, so a kind added here is answered in both
# directions at once. A kind that is not here is a declaration, a terminal or the bare
# name, and the gate refuses a fourth answer.
#
# A kind is keyed by NAME, so a type that lives in `semantics/generics` needs no import:
# a template is not in the `Type` union, and importing one here for an `isinstance` is
# what kept the generic kinds in a dispatch of their own.
COMPOSITE_KINDS: dict[str, tuple[str, ...]] = {
    "ArrayType": ("base_type",),
    "DynamicArrayType": ("base_type",),
    "ReferenceType": ("referenced_type",),
    "PointerType": ("pointee_type",),
    "IteratorType": ("element_type",),
    "FunctionType": ("param_types", "ok_type", "err_type"),
    "GenericTypeRef": ("type_args",),
    "TypePack": ("types",),
}


# A kind whose contents are stored INLINE, i.e. that contribute to the size of the value.
# `inline_only=True` enters these alone: a heap buffer, a pointer and a fat pointer are an
# indirection, and a rule about SIZE stops at one. An `UnknownType` is a NAME rather than a
# kind of storage, so it is entered too -- it resolves to the declaration the name means,
# and that declaration is stored inline wherever the name is written.
INLINE_KINDS = frozenset(
    {"ArrayType", "StructType", "EnumType", "GenericStructType", "GenericEnumType",
     "UnknownType"}
)


# A kind that DECLARES a name, as opposed to one that merely spells it. The cycle guard
# keys on these alone: an `UnknownType("Node")` also carries the name, and letting it
# consume the name would stop the walk before the declaration it resolves to is entered.
DECLARATION_KINDS = frozenset(
    {"StructType", "EnumType", "GenericStructType", "GenericEnumType"}
)


# A kind that SPELLS a declaration without being one. A `resolve` given to the walk maps
# these, and only these, to the declaration they name.
RESOLVABLE_KINDS = frozenset({"UnknownType", "GenericTypeRef"})


def _nominal_name(ty: Type) -> Optional[str]:
    """The name a type is identified BY, when it declares one.

    Type identity is nominal (`docs/design/type-identity.md`), so a name is what the cycle
    guard keys on -- two spellings of one name are one type.
    """
    if type(ty).__name__ not in DECLARATION_KINDS:
        return None
    name = getattr(ty, "name", None)
    return name if isinstance(name, str) else None


def _held_types(ty: Type) -> Iterator[Type]:
    """The types a composite kind holds, in the order its arm names them."""
    for slot in COMPOSITE_KINDS[type(ty).__name__]:
        held = getattr(ty, slot)
        if isinstance(held, tuple):
            yield from held
        elif held is not None:
            yield held


def _declared_types(ty: Type) -> Iterator[Type]:
    """What a declaration holds: a struct's field types, or an enum's payload types.

    A template spells these the way the type it instantiates does, so one reader serves
    `StructType` with `GenericStructType` and `EnumType` with `GenericEnumType`.
    """
    fields = getattr(ty, "fields", None)
    if fields is not None:
        for _field_name, field_type in fields:
            yield field_type
        return
    for variant in getattr(ty, "variants", ()) or ():
        yield from variant.associated_types


def walk_named_types(
    ty: Optional[Type],
    structs: Optional[dict] = None,
    enums: Optional[dict] = None,
    _visited: Optional[Set[str]] = None,
    *,
    through_declarations: bool = True,
    inline_only: bool = False,
    stop: Optional[Callable[[Type], bool]] = None,
    resolve: Optional[Callable[[Type], Optional[Type]]] = None,
    struct_type_args: bool = False,
) -> Iterator[Type]:
    """Every type reachable from `ty`, `ty` itself first.

    `structs` and `enums` let a bare `UnknownType` name resolve to its declaration and be
    walked through. Without them a bare name is a leaf: a caller that has no tables gets
    the name, not a crash.

    `through_declarations=False` stops at a NAMED declaration: it is yielded, but its
    fields and variants are not entered. A rule about what a signature hands out wants
    that, because what a named type holds is its own declaration's business and is fenced
    there; a rule about what a type CONTAINS wants the default.

    `inline_only=True` enters `INLINE_KINDS` alone, so the walk reaches what the value
    STORES and stops at every indirection. A rule about SIZE wants that: `Node[]` owns a
    heap buffer and holds no `Node` by value, while `Node[2]` holds two.

    `stop` is the prune hook: a type it answers True for is yielded, and nothing it holds
    is entered. A rule about ownership stops at a borrow, because a borrow names storage
    that another value owns.

    `resolve` maps a bare `UnknownType` or a `GenericTypeRef` to the declaration it names,
    and the walk enters that declaration instead. A `GenericTypeRef` that does not resolve
    is entered through its type arguments.

    `struct_type_args=True` also enters a struct's `generic_args`. A container keeps its
    element type there, because its fields are raw pointers and a placeholder.
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

    kind = type(ty).__name__
    if inline_only and kind not in INLINE_KINDS:
        return
    if stop is not None and stop(ty):
        return

    def below(inner: Optional[Type]) -> Iterator[Type]:
        yield from walk_named_types(inner, structs, enums, _visited,
                                    through_declarations=through_declarations,
                                    inline_only=inline_only, stop=stop, resolve=resolve,
                                    struct_type_args=struct_type_args)

    if resolve is not None and kind in RESOLVABLE_KINDS:
        named = resolve(ty) or ty
        if named is not ty and named != ty:
            yield from below(named)
            return

    if kind in COMPOSITE_KINDS:
        for held in _held_types(ty):
            yield from below(held)
    elif kind in DECLARATION_KINDS:
        if through_declarations:
            if struct_type_args and kind == "StructType":
                for held in getattr(ty, "generic_args", None) or ():
                    yield from below(held)
            for held in _declared_types(ty):
                yield from below(held)
    elif isinstance(ty, UnknownType):
        resolved = None
        if structs and ty.name in structs:
            resolved = structs[ty.name]
        elif enums and ty.name in enums:
            resolved = enums[ty.name]
        if resolved is not None:
            yield from below(resolved)


def map_named_types(
    ty: Optional[Type],
    resolve: Callable[[Type], Type],
) -> Optional[Type]:
    """`ty` rebuilt with `resolve` applied to it and to every type it holds.

    The REBUILDING direction over the arm table the walk reads. The resolver used to carry
    an arm set of its own, four against the walk's thirteen, so a name in a reference, a
    pointer or an iterator came back unresolved while the same name in an array came back
    as the table entry (#716).

    A DECLARATION is where the map stops. Type identity is nominal, so a named type is
    looked up and never rebuilt: a second instance of one name is a second spelling of one
    type, and it cannot terminate for a self-reference (#240,
    `docs/design/type-identity.md`). That is also why the map needs no cycle guard.

    Nothing moved means nothing is rebuilt and the same object comes back, because the
    resolve pass runs again at every late intern.
    """
    if ty is None:
        return None

    mapped = resolve(ty)
    slots = COMPOSITE_KINDS.get(type(mapped).__name__)
    if slots is None:
        return mapped

    changed = {}
    for slot in slots:
        held = getattr(mapped, slot)
        if isinstance(held, tuple):
            new_held: object = tuple(map_named_types(item, resolve) for item in held)
        else:
            new_held = map_named_types(held, resolve)
        if new_held != held:
            changed[slot] = new_held

    if not changed:
        return mapped
    # `replace`, so what a kind carries BESIDE the types it holds rides along: a fixed
    # array's size, a reference's borrow mode, and a fn type's `captures` and
    # `param_modes` (#368).
    return replace(mapped, **changed)
