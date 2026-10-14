"""The SHIR unit: one body, its parameters, its locals and its copy boundaries (docs/design/ir.md 6.3, 7.2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TypeAlias

from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.types import TemplateId
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.ids import DeclId, LocalId, ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock, TypeRef


@dataclass(frozen=True, slots=True)
class Instance:
    """The body of a function or a method, concrete or a monomorphized copy."""


@dataclass(frozen=True, slots=True)
class TemplateCheck:
    """The check copy of one template, at opaque type arguments (ir.md 4.3)."""
    template: TemplateId


@dataclass(frozen=True, slots=True)
class Initializer:
    """The one-expression body of a constant or a `var` initializer (ir.md 6.3)."""


BodyOrigin: TypeAlias = Instance | TemplateCheck | Initializer


@dataclass(slots=True)
class ShirParam:
    """`pack` is true for a value pack parameter: the source writes `...Ts args`."""
    local: LocalId
    ty: TypeRef
    convention: Convention
    loc: Span
    pack: bool


@dataclass(slots=True)
class ShirLocal:
    """`name` is None for a hidden local. `declared` is the type as WRITTEN, if any."""
    id: LocalId
    name: Optional[str]
    declared: Optional[TypeRef]
    loc: Optional[Span]


@dataclass(frozen=True, slots=True)
class CopyRange:
    """The statements `start` to `stop` (not included) of a block: one copy of an `expand`."""
    start: int
    stop: int


@dataclass(slots=True)
class ExpandCopies:
    """The copy boundaries of each block that holds copies of an unrolled `expand` (ir.md 7.2).

    The monomorphize pass puts one copy of the body of an `expand` in the enclosing block
    for each element of the pack, as ordinary statements. A copy of a nested `expand` is in
    a copy of the outer one, so the ranges of a block are in the order of their start, the
    outer copy first. Body lowering is the one writer (gate:
    `tests/unit/test_shir_expand_copies_have_one_writer.py`).
    """
    _copy_ranges: dict[ShirId, tuple[CopyRange, ...]] = field(default_factory=dict)

    def record_copies(self, block: ShirId, ranges: tuple[CopyRange, ...]) -> None:
        self._copy_ranges[block] = ranges

    def blocks(self) -> list[ShirId]:
        """Each block that holds a copy, in id order."""
        return sorted(self._copy_ranges)

    def ranges_of(self, block: ShirId) -> tuple[CopyRange, ...]:
        """The copies in `block`, in the order of their start, the outer copy first."""
        return self._copy_ranges.get(block, ())

    def holding(self, block: ShirId, index: int) -> tuple[CopyRange, ...]:
        """The copies that hold the statement `index` of `block`, the outer copy first.

        Empty for a written statement. Two statements with different answers were never
        written one after the other.
        """
        return tuple(found for found in self.ranges_of(block) if found.start <= index < found.stop)


@dataclass(slots=True)
class ShirBody:
    fn_id: DeclId
    origin: BodyOrigin
    params: list[ShirParam]
    locals: list[ShirLocal]
    block: ShirBlock
    copies: ExpandCopies
