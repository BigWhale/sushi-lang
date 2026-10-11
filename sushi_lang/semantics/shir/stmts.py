"""The SHIR statement nodes and the parts that they hold (docs/design/ir.md 7.5).

Data only. A node holds what the source says; which walk a loop gets and what a
destructure owns are answers of the passes, in their own tables (rulings S5, S6). The
union `ShirStmt` is every statement node and the hole, and a dispatch reads it (gate:
`tests/unit/test_shir_node_unions_are_total.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, TypeAlias

from sushi_lang.semantics.shir.exprs import ShirExpr
from sushi_lang.semantics.shir.ids import LocalId
from sushi_lang.semantics.shir.nodes import Hole, ShirBlock, ShirNode
from sushi_lang.semantics.typesys import BorrowMode

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.patterns import Arm


@dataclass(slots=True)
class Assign(ShirNode):
    """A `let` (`is_init`) or a rebind. A rebind frees the old value (ir.md 4.6)."""
    target: ShirExpr
    value: ShirExpr
    is_init: bool


@dataclass(frozen=True, slots=True)
class Discard:
    """`_` in a destructure, or as the binder of a loop: nothing is bound."""


@dataclass(frozen=True, slots=True)
class Nested:
    """A nested destructure: `(a, b)` in `let ((a, b), c) = v`."""
    targets: list[DestructureTarget]


DestructureTarget: TypeAlias = LocalId | Discard | Nested


@dataclass(slots=True)
class Destructure(ShirNode):
    """`let (a, b) = v`. It owns from an owned value and borrows from a borrow."""
    targets: list[DestructureTarget]
    value: ShirExpr


@dataclass(slots=True)
class ExprStmt(ShirNode):
    value: ShirExpr


@dataclass(slots=True)
class Return(ShirNode):
    value: ShirExpr


@dataclass(slots=True)
class If(ShirNode):
    """An `elif` is an `If` in `otherwise`."""
    cond: ShirExpr
    then: ShirBlock
    otherwise: Optional[ShirBlock]


@dataclass(frozen=True, slots=True)
class Bare:
    """A binder with no mode: a loop binder or a pattern binding (ir.md 7.5, 7.8)."""


@dataclass(frozen=True, slots=True)
class Ref:
    """A `peek` or `poke` binder: it points into the item or the payload."""
    mode: BorrowMode


ItemMode: TypeAlias = Bare | Ref


@dataclass(frozen=True, slots=True)
class Cond:
    """`while (cond)`."""
    cond: ShirExpr


@dataclass(frozen=True, slots=True)
class Items:
    """`foreach (binder in iterable)`, with the written mode of the binder. `_` is `Discard`."""
    iterable: ShirExpr
    binder: LocalId | Discard
    by: ItemMode


LoopSource: TypeAlias = Cond | Items


@dataclass(slots=True)
class Loop(ShirNode):
    """The walk kind is `typecheck`'s (ir.md 7.7)."""
    source: LoopSource
    body: ShirBlock


@dataclass(slots=True)
class Match(ShirNode):
    """`consumes` is `match nom r:`, which lets a `nom` binding take the payload."""
    scrutinee: ShirExpr
    arms: list[Arm]
    consumes: bool


@dataclass(slots=True)
class Assert(ShirNode):
    """The message is evaluated only when `cond` fails. `label` is the source text."""
    cond: ShirExpr
    message: Optional[ShirExpr]
    label: str


@dataclass(slots=True)
class Break(ShirNode):
    pass


@dataclass(slots=True)
class Continue(ShirNode):
    pass


@dataclass(slots=True)
class Expand(ShirNode):
    """`expand`, in a template check body only (ir.md 4.5)."""
    binder: LocalId
    pack: str
    body: ShirBlock


ShirStmt: TypeAlias = (
    Assign | Destructure | ExprStmt | Return | If | Loop | Match | Assert | Break
    | Continue | Expand | Hole
)
