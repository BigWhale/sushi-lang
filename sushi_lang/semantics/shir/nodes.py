"""The SHIR node base and the block (docs/design/ir.md 7.3, 7.5)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.stmts import ShirStmt

#: A type as WRITTEN (ir.md 7.4). It is syntax, so a node may hold it.
TypeRef: TypeAlias = Type


@dataclass(slots=True)
class ShirNode:
    """Every node has an id and a span, and no type field (ruling S5)."""
    shir_id: ShirId
    loc: Span


@dataclass(slots=True)
class ShirBlock(ShirNode):
    """The statements of one block, in the written order."""
    statements: list[ShirStmt]
