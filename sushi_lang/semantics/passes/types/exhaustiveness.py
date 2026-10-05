"""One exhaustiveness checker for every match (ruling 17 of the tuple design).

The usefulness algorithm of Maranget ("Warnings for pattern matching", 2007) over a
pattern matrix. An arm is one row. A column of an enum type splits into its variants, a
tuple column into its elements, an `Own@(T)` column into its pointee, and an integer
column and a string column have no end of values, so only a `_` or a binding covers
them. Two answers come out: the values that no arm matches (the witnesses), and the
arms that no value can reach.
The design is in docs/design/tuples.md section 5b.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple, Union

from sushi_lang.semantics.typesys import Type


@dataclass(frozen=True)
class Wild:
    """A position that matches every value: `_`, a binding, `Own(x)`."""


WILD = Wild()

# The key of the one constructor of a tuple and of an `Own@(T)`. A variant's key is its
# name, an integer literal's key is its value, and a string literal's key is
# `string_key(value)`.
TUPLE_KEY = ("tuple",)
OWN_KEY = ("own",)


def string_key(value: str) -> Tuple[str, str]:
    """The key of a string literal: it never equals a variant name or an integer."""
    return ("string", value)


@dataclass(frozen=True)
class Ctor:
    """A position that matches one constructor of its type, and its sub-positions."""
    key: object
    args: Tuple["Pat", ...] = ()


Pat = Union[Wild, Ctor]

# The constructors of a type, each with the types of its sub-positions, or None when the
# values of the type cannot be listed (an integer, a string, a struct).
Signature = Optional[Sequence[Tuple[object, Tuple[Type, ...]]]]

# A bound on the number of witnesses. A wide tuple of enums has a product of missing
# combinations, and a diagnostic names a few of them.
WITNESS_LIMIT = 16


@dataclass(frozen=True)
class Analysis:
    """The two answers: the missing values, and each dead arm with the arms covering it."""
    missing: List[Pat]
    dead: List[Tuple[int, List[int]]]


class PatternMatrix:
    """The usefulness and witness questions over rows of patterns."""

    def __init__(self, signature: Callable[[Type], Signature]) -> None:
        """`signature` answers the constructors of a column type."""
        self.signature = signature

    def _arg_types(self, key: object, ty: Type) -> Tuple[Type, ...]:
        for ctor_key, arg_types in self.signature(ty) or ():
            if ctor_key == key:
                return tuple(arg_types)
        return ()

    @staticmethod
    def _specialize(rows: List[List[Pat]], key: object, arity: int) -> List[List[Pat]]:
        out = []
        for row in rows:
            head = row[0]
            if isinstance(head, Ctor):
                if head.key == key:
                    out.append(list(head.args) + row[1:])
            else:
                out.append(_wilds(arity) + row[1:])
        return out

    @staticmethod
    def _default(rows: List[List[Pat]]) -> List[List[Pat]]:
        return [row[1:] for row in rows if not isinstance(row[0], Ctor)]

    def _complete(self, rows: List[List[Pat]], ty: Type):
        """The signature of the first column when the rows name all of it, else None."""
        signature = self.signature(ty)
        if signature is None:
            return None, set()
        heads = {row[0].key for row in rows if isinstance(row[0], Ctor)}
        if all(key in heads for key, _ in signature):
            return signature, heads
        return None, heads

    def useful(self, rows: List[List[Pat]], vector: List[Pat], types: List[Type]) -> bool:
        """Does `vector` match a value that no row matches?"""
        if not vector:
            return not rows
        head = vector[0]
        if isinstance(head, Ctor):
            arg_types = self._arg_types(head.key, types[0])
            return self.useful(self._specialize(rows, head.key, len(head.args)),
                               list(head.args) + vector[1:],
                               list(arg_types) + types[1:])
        signature, _heads = self._complete(rows, types[0])
        if signature is not None:
            return any(
                self.useful(self._specialize(rows, key, len(arg_types)),
                            _wilds(len(arg_types)) + vector[1:],
                            list(arg_types) + types[1:])
                for key, arg_types in signature)
        return self.useful(self._default(rows), vector[1:], types[1:])

    def missing(self, rows: List[List[Pat]], types: List[Type],
                limit: int = WITNESS_LIMIT) -> List[List[Pat]]:
        """Value vectors that no row matches, at most `limit` of them."""
        if not types:
            return [] if rows else [[]]
        signature, heads = self._complete(rows, types[0])
        if signature is not None:
            found: List[List[Pat]] = []
            for key, arg_types in signature:
                arity = len(arg_types)
                for witness in self.missing(self._specialize(rows, key, arity),
                                            list(arg_types) + types[1:],
                                            limit - len(found)):
                    head: List[Pat] = [Ctor(key, tuple(witness[:arity]))]
                    found.append(head + witness[arity:])
                    if len(found) >= limit:
                        return found
            return found
        rest = self.missing(self._default(rows), types[1:], limit)
        if not rest:
            return []
        listed = self.signature(types[0])
        if listed is None or not heads:
            fills: List[Pat] = [WILD]
        else:
            fills = [Ctor(key, (WILD,) * len(arg_types))
                     for key, arg_types in listed if key not in heads]
        return [[fill] + witness for fill in fills for witness in rest][:limit]


def _wilds(count: int) -> List[Pat]:
    return [WILD] * count


def intersects(first: Pat, second: Pat) -> bool:
    """Is there a value that both patterns match?"""
    if isinstance(first, Wild) or isinstance(second, Wild):
        return True
    return (first.key == second.key and len(first.args) == len(second.args)
            and all(intersects(a, b) for a, b in zip(first.args, second.args, strict=True)))


def analyze(signature: Callable[[Type], Signature], rows: Sequence[Optional[Pat]],
            scrutinee_type: Type) -> Analysis:
    """Check one match: `rows[i]` is arm i's pattern, or None for an arm left out.

    An arm is dead when it is not useful against the arms above it. The arms above that
    share a value with it cover it together, because an arm that shares no value with it
    covers none of its values.
    """
    matrix = PatternMatrix(signature)
    seen: List[List[Pat]] = []
    dead: List[Tuple[int, List[int]]] = []
    for index, row in enumerate(rows):
        if row is None:
            continue
        if not matrix.useful(seen, [row], [scrutinee_type]):
            covers = [above for above, other in enumerate(rows[:index])
                      if other is not None and intersects(other, row)]
            dead.append((index, covers))
        seen.append([row])
    missing = [witness[0] for witness in matrix.missing(seen, [scrutinee_type])]
    return Analysis(missing=missing, dead=dead)
