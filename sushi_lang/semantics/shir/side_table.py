"""The shapes that every side table of a SHIR body shares (docs/design/ir.md 7.3, 7.9).

A side table holds the answers of one pass for one body: `TypeckResults`, `OwnershipResults`
and `LiftResults`. Each obeys the four rules of ir.md 7.3, and this module holds the parts
of those rules that the tables share: a key has one answer (`write_once`), a table is not
copied (`SideTable`), and the completeness check of a table names its first gap with an
internal error (`MissingAnswer`, `raise_first_gap`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence, TypeVar

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors.registry import ErrorMessage
from sushi_lang.internals.report import Span

KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


def write_once(table: dict[KeyT, ValueT], key: KeyT, value: ValueT, writer: str,
               row: str) -> None:
    """Write the answer of `key`. A second answer for one key is an internal error."""
    if key in table:
        er.raise_internal_error("CE0000", detail=f"the {writer} pass answered the {row} of "
                                                 f"{key} twice")
    table[key] = value


def row_table() -> Any:
    """A table of one row kind: closed to the constructor, as a pass writes it."""
    return field(default_factory=dict, init=False, repr=False)


class SideTable:
    """The base of a side table: a copy is a second table of one body, so it is refused."""
    __slots__ = ()

    def __copy__(self) -> Any:
        er.raise_internal_error("CE0000", detail=f"a copy of a {type(self).__name__} is a "
                                                 "second table of one body")

    def __deepcopy__(self, memo: dict[int, object]) -> Any:
        return self.__copy__()


@dataclass(frozen=True, slots=True)
class MissingAnswer:
    """An answer that a node needs and that the table does not hold."""
    answer: str
    node: str
    loc: Optional[Span]


def raise_first_gap(message: ErrorMessage, gaps: Sequence[MissingAnswer], body: str,
                    **params: str) -> None:
    """The internal error `message` at the first gap, if any: the answer, the class of the
    node, its position and the name of the body, beside the `params` of the code."""
    if not gaps:
        return
    gap = gaps[0]
    where = "no location" if gap.loc is None else f"line {gap.loc.line}, column {gap.loc.col}"
    er.raise_internal_error(message.code, span=gap.loc, **params, answer=gap.answer,
                            node=gap.node, where=where, body=body)
