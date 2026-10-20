"""The body walk of the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.9).

ONE table, `ARMS`, gives the arm of each SHIR node class: each member of `ShirStmt`,
`ShirExpr` and `ShirPattern` (gate: `tests/unit/test_typecheck_table_is_total.py`). An arm
takes the pass and one node, and it writes its answers into the `TypeckResults` of the
body (`TypeValidator.answers`). `check` is the one dispatch, and a class with no row is an
internal error. The arm of a construct that the pass does not type yet is `stop_at`: it
stops the walk of the body with the stop code CE0155 (`shir/stop.py`).

`statements.py` walks a block. The AST inference visitor of `inference.py` is not part of
this walk: it is the inference of the early passes (`ReadOnlyInferrer`), until they stop
inferring. `helpers.py` holds the lambda and function-value helpers that it
reads.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, NoReturn, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import Hole
from sushi_lang.semantics.shir.stop import StageStop

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

#: The name of this stage in the stop code.
STAGE = "typecheck"

Arm: TypeAlias = Callable[["TypeValidator", Any], None]


def stop_at(_validator: TypeValidator, node: Any) -> NoReturn:
    """The arm of a construct that the pass does not type yet: the walk of the body stops."""
    raise StageStop(STAGE, type(node).__name__, node.loc)


def check(validator: TypeValidator, node: Any) -> None:
    """Check one node through its arm."""
    arm = ARMS.get(type(node))
    if arm is None:
        er.raise_internal_error(
            "CE0000", detail=f"the typecheck pass has no arm for the SHIR class "
                             f"'{type(node).__name__}'")
    arm(validator, node)


ARMS: dict[type, Arm] = {
    # -- statements ------------------------------------------------------------------
    s.Assign: stop_at,
    s.Destructure: stop_at,
    s.ExprStmt: stop_at,
    s.Return: stop_at,
    s.If: stop_at,
    s.Loop: stop_at,
    s.Match: stop_at,
    s.Assert: stop_at,
    s.Break: stop_at,
    s.Continue: stop_at,
    s.Expand: stop_at,
    Hole: stop_at,
    # -- expressions -----------------------------------------------------------------
    e.Lit: stop_at,
    e.LocalRef: stop_at,
    e.ConstRef: stop_at,
    e.StorageRef: stop_at,
    e.GlobalRef: stop_at,
    e.Field: stop_at,
    e.Index: stop_at,
    e.Slice: stop_at,
    e.Call: stop_at,
    e.Unary: stop_at,
    e.Binary: stop_at,
    e.Cast: stop_at,
    e.Borrow: stop_at,
    e.Take: stop_at,
    e.Try: stop_at,
    e.Lambda: stop_at,
    e.StructInit: stop_at,
    e.EnumInit: stop_at,
    e.TupleInit: stop_at,
    e.ArrayInit: stop_at,
    e.Range: stop_at,
    e.Interp: stop_at,
    # -- patterns --------------------------------------------------------------------
    p.Wildcard: stop_at,
    p.Literal: stop_at,
    p.IntRange: stop_at,
    p.Variant: stop_at,
    p.Tuple: stop_at,
    p.Own: stop_at,
    p.Or: stop_at,
}
