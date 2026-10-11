"""`Iterator@(T)`: the item mode, and the one type that a plain item makes.

The compiler's own type, as `HashMap@(K, V)` is (docs/design/iteration.md). The item is
OWNED (`Iterator@(T)`) or LENT (`Iterator@(peek T)`); a tuple item takes the mode per
element (`Iterator@((i32, peek string))`). `IteratorType.peeks` holds the mode, and
`IteratorType.element_type` holds the value type with no `peek` in it.

Three directions, one module: a WRITTEN item to the type (`iterator_of`, the AST builder
and the manifest reader), the type back to its written item (`written_item`, the interned
name and the display), and the identity rule for a plain item (`settle_item_modes`).
"""
from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import AbstractSet, Any, Callable, Dict, Mapping, Optional, Tuple, cast

from sushi_lang.semantics.generics.tuples import is_tuple_type, tuple_elements, tuple_ref
from sushi_lang.semantics.typesys import BorrowMode, IteratorType, ReferenceType, Type

ITERATOR_BASE = "Iterator"

# The built-in producers of an iterator over a container: `.iter()` on `T[]`, `T[N]` and
# `List@(T)`, and the four HashMap walks. Each answers `Iterator@(peek T)`.
CONTAINER_ITERATORS = frozenset({"iter", "keys", "values", "entries", "pairs"})

# How a producer call keeps the state of its iterator (`MethodCall.iterator_state`). The
# typecheck pass stamps CURSOR on the iterable of a direct walk; the borrow pass stamps
# LENT or OWNED on every other producer, and LENT on the clone of a lent iterator.
CURSOR = "cursor"   # the bare cursor a direct `foreach` walk reads; no value is made
LENT = "lent"       # a value that borrows the owners of its source; stack state
OWNED = "owned"     # a value that takes its source (a temporary); heap state


# The built-in methods of the `Iterator` family: `it.clone()` on a value, and the static
# `Iterator.new(nom x)` on the type name. `new` takes its argument with `nom`.
ITERATOR_METHOD_ARITY: Mapping[str, int] = MappingProxyType({"clone": 0, "new": 1})


def iterator_method_modes() -> Any:
    """The parameter modes of the family (`BuiltinModes`): `new` takes `nom x`."""
    from sushi_lang.semantics.param_modes import BuiltinModes, ParamMode
    return BuiltinModes(rows=MappingProxyType({"new": (ParamMode.NOM,)}))


def lent_iterator(item: Type) -> IteratorType:
    """`Iterator@(peek T)`: the type a built-in producer answers over items of `item`."""
    return iterator_of(_lent(item))

# A part of an item that names one of these is not concrete: its mode is decided at the
# instance, never in a template (the template does not know whether `T` owns).
_OPEN_KINDS = frozenset({"TypeParameter", "UnknownType", "GenericTypeRef", "TypePack"})


def iterator_of(item: Type, *, base_name: Optional[str] = None,
                namespace: Optional[str] = None) -> IteratorType:
    """The iterator over a WRITTEN item: a `peek` on the item, or on a tuple element.

    A lent tuple item lends each element, so a tuple item has one spelling. The caller
    has refused every other borrow: `poke`, and `peek` on a tuple written whole.
    """
    if isinstance(item, ReferenceType):
        if is_tuple_type(item.referenced_type):
            elements = tuple_elements(item.referenced_type)
            return IteratorType(element_type=item.referenced_type,
                                peeks=(True,) * len(elements),
                                base_name=base_name, namespace=namespace)
        return IteratorType(element_type=item.referenced_type, peeks=(True,),
                            base_name=base_name, namespace=namespace)
    if is_tuple_type(item):
        elements = tuple_elements(item)
        peeks = tuple(isinstance(element, ReferenceType) for element in elements)
        if any(peeks):
            values = (element.referenced_type if isinstance(element, ReferenceType)
                      else element for element in elements)
            return IteratorType(element_type=tuple_ref(values), peeks=peeks,
                                base_name=base_name, namespace=namespace)
    return IteratorType(element_type=item, base_name=base_name, namespace=namespace)


def read_iterator_string(text: str, read: Callable[[str], Type]) -> Optional[IteratorType]:
    """The iterator an interned name spells (`Iterator<peek string>`), else None.

    `read` reads one type with no mode. The mode is read here, at the top of the item
    or of one tuple element, which is where `written_item` writes it.
    """
    from sushi_lang.semantics.generics.tuples import TUPLE_BASE
    from sushi_lang.semantics.generics.type_strings import split_type_arguments

    item = _arguments_of(text, ITERATOR_BASE)
    if item is None:
        return None
    word = f"{BorrowMode.PEEK} "

    def part(spelled: str) -> Type:
        return _lent(read(spelled[len(word):])) if spelled.startswith(word) else read(spelled)

    elements = _arguments_of(item, TUPLE_BASE)
    if elements is not None:
        written = split_type_arguments(elements)
        if any(element.startswith(word) for element in written):
            return iterator_of(tuple_ref(part(element) for element in written))
    return iterator_of(part(item))


def _arguments_of(text: str, base: str) -> Optional[str]:
    """The argument text of an interned name of `base`, or None for another name."""
    head, angle, rest = text.strip().partition("<")
    if head.strip() != base or not angle or not rest.endswith(">"):
        return None
    return rest[:-1].strip()


def written_item(iterator: IteratorType) -> Type:
    """The item as a program writes it: `peek string`, or `(i32, peek string)`."""
    if not iterator.peeks:
        return iterator.element_type
    if len(iterator.peeks) == 1:
        return _lent(iterator.element_type)
    return tuple_ref(_lent(part) if lent else part
                     for part, lent in zip(item_parts(iterator), iterator.peeks,
                                           strict=True))


def item_parts(iterator: IteratorType) -> Tuple[Type, ...]:
    """The parts the item mode describes: the item, or each element of a tuple item."""
    if len(iterator.peeks) > 1:
        return tuple_elements(iterator.element_type)
    return (iterator.element_type,)


def settle_item_modes(ty: Optional[Type], owns: Callable[[Type], bool]) -> Optional[Type]:
    """`ty` with every iterator in it in its one form: a lent part that owns nothing is owned.

    For a plain item `Iterator@(peek T)` and `Iterator@(T)` are one type. `owns` is the
    caller's `owns_resource`, with the program's `Drop` set and its resolver. A part that
    is not concrete keeps its mode, because only an instance knows what its `T` holds.
    """
    from sushi_lang.semantics.type_walk import map_named_types

    def settle(held: Type) -> Type:
        if not isinstance(held, IteratorType) or not held.peeks:
            return held
        peeks = tuple(lent and not (_is_concrete(part) and not owns(part))
                      for part, lent in zip(item_parts(held), held.peeks, strict=True))
        return held if peeks == held.peeks else replace(held, peeks=peeks)

    return map_named_types(ty, settle)


def item_owner(drops: Callable[[], AbstractSet[str]], structs: Mapping[str, Any],
               enums: Mapping[str, Any]) -> Callable[[Type], bool]:
    """Does a part of an item own a resource: `owns_resource` over the tables.

    `drops` reads the program's `Drop` set when it is asked, so one owner serves a pass
    that runs while the perk table still grows. The one builder of the answer for every
    caller of `settle_item_modes`.
    """
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    from sushi_lang.semantics.typesys import owns_resource

    def owns(part: Type) -> bool:
        return owns_resource(part, drops(), resolve=lambda held: resolve_unknown_type(
            held, cast(Dict[str, Any], structs), cast(Dict[str, Any], enums)))

    return owns


def install_item_owner(tables: Any) -> None:
    """Give the program's enum table the owner of an item, for the interning seams.

    An instance that holds an iterator is interned with its items in their one form
    (`settle_in_tables`), so `Maybe@(Iterator@(peek i32))` and `Maybe@(Iterator@(i32))`
    are one instance. The owner is carried on the table, as `EnumTable.derived` is,
    because those seams hold the table and nothing more.
    """
    from sushi_lang.semantics.drop_set import drop_type_names

    tables.enums.item_owner = item_owner(lambda: drop_type_names(tables.perk_impls),
                                         tables.structs.by_name, tables.enums.by_name)


def settle_in_tables(ty: Type, enum_table: Any) -> Type:
    """`ty` with its items in their one form, by the owner the table carries.

    A table with no owner (a scratch table, the stdlib build) leaves every mode as it is.
    """
    owner = getattr(enum_table, "item_owner", None)
    if owner is None:
        return ty
    settled = settle_item_modes(ty, owner)
    return ty if settled is None else settled


def _lent(part: Type) -> ReferenceType:
    return ReferenceType(referenced_type=part, mutability=BorrowMode.PEEK)


def _is_concrete(part: Type) -> bool:
    from sushi_lang.semantics.type_walk import walk_named_types

    return not any(type(held).__name__ in _OPEN_KINDS
                   for held in walk_named_types(part, through_declarations=False))
