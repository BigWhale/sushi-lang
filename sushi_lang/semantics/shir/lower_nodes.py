"""Body lowering: each AST statement and expression gives one SHIR node (docs/design/ir.md 7.10).

ONE table, `LOWERING`, gives the lowering of each AST body class that lowering handles,
keyed on the class (gate: `tests/unit/test_shir_lowering_is_one_table.py`). A class with
no row is a HOLE: lowering makes a `Hole` that names the AST class, records it, and the
compile goes on through the old pipeline. When the variable that `STRICT_VARIABLE` names
is `1`, a hole stops the compile with the stop code CE0155 instead.

Every id comes from the counter of the body in the order of the walk, a node before its
operands, so a body gives the same ids on every run (ir.md 11.3).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, TypeAlias, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir.bindings import Bindings
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.exprs import ShirExpr
from sushi_lang.semantics.shir.ids import IdAllocator, ShirId
from sushi_lang.semantics.shir.nodes import Hole
from sushi_lang.semantics.shir.stmts import ShirStmt

#: The variable that makes a hole stop the compile. One place reads it (gate:
#: `tests/unit/test_shir_strict_is_read_once.py`).
STRICT_VARIABLE = "SUSHI_SHIR_STRICT"

#: The name of this stage in the stop code.
STAGE = "lowering"


def strict() -> bool:
    """True when a hole stops the compile (docs/compiler-reference.md, Dump SHIR)."""
    return os.environ.get(STRICT_VARIABLE) == "1"


@dataclass(slots=True)
class BodyLowerer:
    """The state of the lowering of one body.

    `holes` is the record of the program, shared by every body. `fallback` is the span of
    a node that has none of its own: the span of its declaration.
    """
    ids: IdAllocator[ShirId]
    decls: DeclNames
    bindings: Bindings
    holes: list[Hole]
    fallback: Span

    def stmt(self, node: Any) -> ShirStmt:
        row = LOWERING.get(type(node))
        return self.hole(node) if row is None else cast(ShirStmt, row(self, node))

    def expr(self, node: Any) -> ShirExpr:
        row = LOWERING.get(type(node))
        return self.hole(node) if row is None else cast(ShirExpr, row(self, node))

    def span(self, node: Any) -> Span:
        loc = getattr(node, "loc", None)
        return self.fallback if loc is None else loc

    def hole(self, node: Any) -> Hole:
        """A construct that lowering does not handle yet; CE0155 under the strict variable."""
        made = Hole(self.ids.allocate(), self.span(node), type(node).__name__)
        if strict():
            er.raise_internal_error("CE0155", span=made.loc, stage=STAGE,
                                    construct=made.ast_class)
        self.holes.append(made)
        return made


Lower: TypeAlias = Callable[[BodyLowerer, Any], "ShirExpr | ShirStmt"]

#: The lowering of each AST body class that lowering handles. A class with no row is a hole.
LOWERING: dict[type, Lower] = {}
