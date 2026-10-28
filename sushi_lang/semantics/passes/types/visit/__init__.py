"""The body walk of the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.9).

ONE table, `ARMS`, gives the arm of each SHIR node class: each member of `ShirStmt`,
`ShirExpr` and `ShirPattern` (gate: `tests/unit/test_typecheck_table_is_total.py`). A
statement arm takes the pass and one node (`check`). An expression arm takes the type of
its position too, and answers the type of its node (`check_expr`), which the dispatch
writes into the `TypeckResults` of the body. The lookup of an arm and the stop are the one
dispatch of the SHIR passes (`shir/dispatch.py`), which the borrow pass reads too: a class
with no row is an internal error, and the arm of a construct that the pass does not type
yet is `stop_at`, which stops the walk of the body with the stop code CE0155.

`statements.py` walks a block and holds the statement arms, `expressions.py` the expression
arms of the leaves, `operators.py` the arms of the operators and the check of a condition,
`calls.py` the arms of a call, an interpolated string and a borrow, and `results.py` the
arm of an enum construction. The pattern arms are in `passes/types/matching.py`: a pattern
arm takes the type of its position, and answers the place of the pattern in the matrix of
the one checker, which no type row holds. The arms of `??` and of `as` are `check_try` and
`check_cast` of `passes/types/expressions.py`, because the callers of the conversion lookup
are in that module. The AST inference visitor of `inference.py` is not part of this walk: it
is the inference of the early passes (`ReadOnlyInferrer`), until they stop inferring.
`helpers.py` holds the lambda and function-value helpers that it reads.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, NoReturn, Optional

from sushi_lang.semantics.passes.types.visit import calls as ca
from sushi_lang.semantics.passes.types.visit import expressions as ex
from sushi_lang.semantics.passes.types.visit import operators as op
from sushi_lang.semantics.passes.types.visit import results as rs
from sushi_lang.semantics.passes.types.visit import statements as st
from sushi_lang.semantics.passes.types import matching as mt
from sushi_lang.semantics.const_eval import ScalarConstant
from sushi_lang.semantics.passes.types.call_facts import TypecheckCallFacts
from sushi_lang.semantics.passes.types.expressions import check_cast, check_try
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import Hole
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

#: The name of this stage in the stop code.
STAGE = "typecheck"


def stop_at(validator: TypeValidator, node: Any, _expected: Optional[Type] = None) -> NoReturn:
    """The arm of a construct that the pass does not type yet: the walk of the body stops."""
    facts = TypecheckCallFacts(validator) if isinstance(node, e.Call) else None
    dispatch.stop(STAGE, node, facts)


def stop_with_no_value(node: Any) -> NoReturn:
    """The stop at a constant that has no value and no error (`ConstRef with no value`), or
    at the `Return` of such an initializer (`Return with no value`): a function-typed
    constant, for example. After an error of the initializer, the error is the diagnostic."""
    dispatch.stop(STAGE, node, state="with no value")


def stop_at_coverage(node: s.Match) -> NoReturn:
    """The stop after the arms of a match on an integer or a string (`Match exhaustiveness`):
    the pass does not check the coverage of the arms yet."""
    dispatch.stop(STAGE, node, state="exhaustiveness")


def _arm(node: Any) -> Callable[..., Any]:
    return dispatch.arm_of(ARMS, node, STAGE)


def check(validator: TypeValidator, node: Any) -> None:
    """Check one statement through its arm."""
    _arm(node)(validator, node)


def check_expr(validator: TypeValidator, node: Any,
               expected: Optional[Type] = None) -> Optional[Type]:
    """Check one expression at the type `expected` of its position, and write its type.

    None is no answer: the node has an error, or reads a node that has one.
    """
    return answer(validator, node, _arm(node)(validator, node, expected))


def answer(validator: TypeValidator, node: Any, ty: Optional[Type],
           wraps: bool = False) -> Optional[Type]:
    """Write `ty` as the type row of the expression `node`, and answer it. The one writer
    of a type row: `check_expr`, the arm of a negation for its literal, which is one value
    with its sign, and the arm of a cast for an integer literal directly under it. Those two
    arms set `wraps`. The value row of an integer literal is written here too, at its type
    (`ex.held_value`, ir.md 8.5)."""
    if ty is not None:
        answers = validator.body_answers()
        answers.record_type(node.shir_id, ty)
        if isinstance(node, e.Lit) and isinstance(node.value, e.IntConst):
            answers.record_value(node.shir_id,
                                 ScalarConstant(ex.held_value(node.value, ty, wraps), ty))
    return ty


ARMS: dict[type, Callable[..., Any]] = {
    # -- statements ------------------------------------------------------------------
    s.Assign: st.check_assign,
    s.Destructure: stop_at,
    s.ExprStmt: st.check_expr_stmt,
    s.Return: st.check_return,
    s.If: st.check_if,
    s.Loop: st.check_loop,
    s.Match: st.check_match,
    s.Assert: st.check_assert,
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
    e.Call: ca.check_call,
    e.Unary: op.check_unary,
    e.Binary: op.check_binary,
    e.Cast: check_cast,
    e.Borrow: ca.check_borrow,
    e.Take: stop_at,
    e.Try: check_try,
    e.Lambda: stop_at,
    e.StructInit: stop_at,
    e.EnumInit: rs.check_enum_init,
    e.TupleInit: stop_at,
    e.ArrayInit: stop_at,
    e.Range: stop_at,
    e.Interp: ca.check_interp,
    # -- patterns --------------------------------------------------------------------
    p.Wildcard: mt.check_wildcard_pattern,
    p.Literal: mt.check_literal_pattern,
    p.IntRange: mt.check_range_pattern,
    p.Variant: stop_at,
    p.Tuple: stop_at,
    p.Own: stop_at,
    p.Or: stop_at,
}
