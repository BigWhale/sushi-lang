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
from typing import Callable, Optional, Tuple

from sushi_lang.semantics.generics.tuples import is_tuple_type, tuple_elements, tuple_ref
from sushi_lang.semantics.typesys import BorrowMode, IteratorType, ReferenceType, Type

ITERATOR_BASE = "Iterator"

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


def _lent(part: Type) -> ReferenceType:
    return ReferenceType(referenced_type=part, mutability=BorrowMode.PEEK)


def _is_concrete(part: Type) -> bool:
    from sushi_lang.semantics.type_walk import walk_named_types

    return not any(type(held).__name__ in _OPEN_KINDS
                   for held in walk_named_types(part, through_declarations=False))
