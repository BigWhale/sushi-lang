"""A chain of copies whose type arguments grow without end (CE0151).

ONE rule for a generic function copy and for a call-site extension copy (an `extend T[]`
method, a method with a method-level type parameter, a late static). Each copy has a key,
and the key of the copy whose body named it is its parent (`Monomorphizer.parents`). A
chain that has no end makes a new copy at each step. The templates are finite, so one
template comes back in the chain again and again, and its type arguments get larger
each time, because a type of one size has finitely many forms. The rule counts the
earlier copies of the same template in the chain that are smaller than the new one. At
`MAX_GROWTH` of them, the chain grows without end.

A finite chain makes no such count: a chain of different templates names each template
one time, a copy that calls itself with the same type arguments is the same key, and a
written type argument (`wrap(nom Box(5))`) grows one time and stops.
"""
from __future__ import annotations

from typing import Iterable, Mapping

from sushi_lang.semantics.type_walk import walk_named_types
from sushi_lang.semantics.typesys import Type

# How many earlier and smaller copies of one template a chain may hold before it is
# judged to grow without end.
MAX_GROWTH = 8


def type_arguments_size(type_args: Iterable[Type]) -> int:
    """How many types the type arguments hold, each one included.

    A type that holds another type is larger than it: `Box@(Box@(i32))` holds three
    types and `Box@(i32)` holds two. A container keeps its element in its type
    arguments, so the walk reads them too.
    """
    size = 0
    for arg in type_args:
        size += len(list(walk_named_types(arg, struct_type_args=True)))
    return size


def grows_without_end(key: object, template: object, size: int,
                      parents: Mapping[object, object],
                      records: Mapping[object, tuple[object, int]]) -> bool:
    """Does the chain that ends at `key` hold `MAX_GROWTH` smaller copies of `template`?

    `records` holds the template and the size of each key that was judged. A parent
    with no record (an extension copy of a written type) counts for nothing.
    """
    smaller = 0
    seen = {key}
    current = parents.get(key)
    while current is not None and current not in seen:
        seen.add(current)
        record = records.get(current)
        if record is not None and record[0] == template and record[1] < size:
            smaller += 1
            if smaller >= MAX_GROWTH:
                return True
        current = parents.get(current)
    return False
