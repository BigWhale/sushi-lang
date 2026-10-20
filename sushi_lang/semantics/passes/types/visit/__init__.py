"""The body walk of the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.9).

ONE table, `ARMS`, gives the arm of each SHIR node class: each member of `ShirStmt`,
`ShirExpr` and `ShirPattern` (gate: `tests/unit/test_typecheck_table_is_total.py`). A
statement arm takes the pass and one node (`check`). An expression arm takes the type of
its position too, and answers the type of its node (`check_expr`), which the dispatch
writes into the `TypeckResults` of the body. A class with no row is an internal error. The
arm of a construct that the pass does not type yet is `stop_at`: it stops the walk of the
body with the stop code CE0155 (`shir/stop.py`).

`statements.py` walks a block and holds the statement arms, `expressions.py` the expression
arms of the leaves, and `operators.py` the arms of the operators and the check of a
condition. The AST inference visitor of `inference.py` is not part of this walk: it is the
inference of the early passes (`ReadOnlyInferrer`), until they stop inferring. `helpers.py`
holds the lambda and function-value helpers that it reads.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, NoReturn, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.semantics.passes.types.visit import expressions as ex
from sushi_lang.semantics.passes.types.visit import operators as op
from sushi_lang.semantics.passes.types.visit import statements as st
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import Hole
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

#: The name of this stage in the stop code.
STAGE = "typecheck"


def stop_at(_validator: TypeValidator, node: Any, _expected: Optional[Type] = None) -> NoReturn:
    """The arm of a construct that the pass does not type yet: the walk of the body stops."""
    raise StageStop(STAGE, type(node).__name__, node.loc)


def _arm(node: Any) -> Callable[..., Any]:
    arm = ARMS.get(type(node))
    if arm is None:
        er.raise_internal_error(
            "CE0000", detail=f"the typecheck pass has no arm for the SHIR class "
                             f"'{type(node).__name__}'")
    return arm


def check(validator: TypeValidator, node: Any) -> None:
    """Check one statement through its arm."""
    _arm(node)(validator, node)


def check_expr(validator: TypeValidator, node: Any,
               expected: Optional[Type] = None) -> Optional[Type]:
    """Check one expression at the type `expected` of its position, and write its type.

    None is no answer: the node has an error, or reads a node that has one.
    """
    return answer(validator, node, _arm(node)(validator, node, expected))


def answer(validator: TypeValidator, node: Any, ty: Optional[Type]) -> Optional[Type]:
    """Write `ty` as the type row of the expression `node`, and answer it. The one writer
    of a type row: `check_expr`, and the arm of a negation for its literal, which is one
    value with its sign."""
    if ty is not None:
        validator.body_answers().record_type(node.shir_id, ty)
    return ty


ARMS: dict[type, Callable[..., Any]] = {
    # -- statements ------------------------------------------------------------------
    s.Assign: st.check_assign,
    s.Destructure: stop_at,
    s.ExprStmt: st.check_expr_stmt,
    s.Return: st.check_return,
    s.If: st.check_if,
    s.Loop: st.check_loop,
    s.Match: stop_at,
    s.Assert: stop_at,
    s.Break: st.check_jump,
    s.Continue: st.check_jump,
    s.Expand: stop_at,
    Hole: stop_at,
    # -- expressions -----------------------------------------------------------------
    e.Lit: ex.check_lit,
    e.LocalRef: ex.check_local_ref,
    e.ConstRef: ex.check_const_ref,
    e.StorageRef: stop_at,
    e.GlobalRef: stop_at,
    e.Field: stop_at,
    e.Index: stop_at,
    e.Slice: stop_at,
    e.Call: stop_at,
    e.Unary: op.check_unary,
    e.Binary: op.check_binary,
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
