"""The SHIR node base, the block and the hole (docs/design/ir.md 7.3, 7.5).

A HOLE stands where lowering cannot handle a construct. It names the AST class of the
construct, and lowering records it (`lower_nodes.py`). It is in the expression union and
in the statement union, because a construct of either level can be one. Lowering is total
over the AST body classes (gate: `tests/unit/test_shir_lowering_is_total.py`), and the
hole stays for two cases: the backstop of a class with no row, and the error hole of a
program that always has an error (a name with no declaration, a form that the language
always refuses), for which SHIR has no node.
"""
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


@dataclass(slots=True)
class Hole(ShirNode):
    """A construct that lowering does not handle yet: `ast_class` names its AST class."""
    ast_class: str
