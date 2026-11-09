"""The body walk of the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.9).

ONE table, `ARMS`, gives the arm of each statement and expression class: each member of
`ShirStmt` and `ShirExpr`. A pattern has its own table, `PATTERN_ARMS`, with a row for each
member of `ShirPattern` (ruling R17; gate for both:
`tests/unit/test_typecheck_table_is_total.py`). A statement arm takes the pass and one node
(`check`). An expression arm takes the type of its position too, and answers the type of
its node (`check_expr`), which the dispatch writes into the `TypeckResults` of the body. A
pattern arm takes the type of its position and a note that says where that type comes from,
and answers the place of the pattern in the matrix of the one checker (`check_pattern`): a
pattern has no type, so its dispatch writes no row. The lookup of an arm is the one
dispatch of the SHIR passes (`shir/dispatch.py`), which the borrow pass reads too: a class
with no row is an internal error, and so is a construct that an arm does not type
(`unhandled`).

`statements.py` walks a block and holds the statement arms, `destructure.py` the arm of a
destructure, `expressions.py` the expression arms of the leaves and of a struct
construction, `fields.py` the arms of a field read and of a tuple, `operators.py` the arms
of the operators and the check of a condition, `calls.py` the arms of a call, an
interpolated string and a borrow, `function_values.py` the arms of a function used as a value
and of a call of a function value, `lambdas.py` the arm of a lambda,
`container_statics.py` the arm of a static of a built-in
container, `results.py` the arm of an enum construction, `constructions.py` the instance of
a construction and of a static of a generic type,
`ranges.py` the arms of a range, and `arrays.py` the arms of an array construction, an
index and a slice. The pattern arms are in `passes/types/matching.py`. The
arms of `??` and of `as` are `check_try` and `check_cast` of
`passes/types/expressions.py`, because the callers of the conversion lookup are in that
module. `helpers.py` holds the lambda helper.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, NoReturn, Optional

from sushi_lang.semantics.passes.types.visit import arrays as ar
from sushi_lang.semantics.passes.types.visit import calls as ca
from sushi_lang.semantics.passes.types.visit import destructure as ds
from sushi_lang.semantics.passes.types.visit import expressions as ex
from sushi_lang.semantics.passes.types.visit import fields as fd
from sushi_lang.semantics.passes.types.visit import function_values as fv
from sushi_lang.semantics.passes.types.visit import lambdas as lm
from sushi_lang.semantics.passes.types.visit import operators as op
from sushi_lang.semantics.passes.types.visit import ranges as rg
from sushi_lang.semantics.passes.types.visit import results as rs
from sushi_lang.semantics.passes.types.visit import statements as st
from sushi_lang.semantics.passes.types import matching as mt
from sushi_lang.semantics.passes.types.matching import PositionNote
from sushi_lang.semantics.const_eval import ScalarConstant
from sushi_lang.semantics.passes.types.exhaustiveness import Pat
from sushi_lang.semantics.passes.types.expressions import check_cast, check_try
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import Hole
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

#: The name of this stage in an internal error.
STAGE = "typecheck"


def unhandled(validator: TypeValidator, node: Any,
              _expected: Optional[Type] = None) -> NoReturn:
    """The internal error of a construct that the pass does not type. After an error, the
    walk of the body ends there."""
    dispatch.unhandled(STAGE, node, after_an_error=validator.has_an_error())


def unhandled_with_no_value(validator: TypeValidator, node: Any) -> NoReturn:
    """The internal error of a constant that has no value and no error, or of the `Return`
    of such an initializer. After an error, the walk of the body ends there."""
    dispatch.unhandled(STAGE, node, state="with no value",
                       after_an_error=validator.has_an_error())


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


def check_pattern(validator: TypeValidator, node: Any, ty: Type,
                  note: Optional[PositionNote] = None) -> Optional[Pat]:
    """Check one pattern at the type `ty` of its position; `note` says where that type comes
    from. The answer is its place in the matrix of the one checker, or None after an error.
    No type row is written."""
    return dispatch.arm_of(PATTERN_ARMS, node, STAGE)(validator, node, ty, note)


def answer(validator: TypeValidator, node: Any, ty: Optional[Type],
           wraps: bool = False) -> Optional[Type]:
    """Write `ty` as the type row of the expression `node`, and answer it. The one writer
    of a type row: `check_expr`, the arm of a negation for its literal, which is one value
    with its sign, the arm of a cast for an integer literal directly under it, and the
    tuple arm for a tuple that a destructure checks part by part (`fields.check_tuple_at`),
    and the range of a `foreach` (`ranges.check_range_iterable`).
    The arms of a negation and of a cast set `wraps`. The value row of an integer literal is written here too, at its type
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
    s.Destructure: ds.check_destructure,
    s.ExprStmt: st.check_expr_stmt,
    s.Return: st.check_return,
    s.If: st.check_if,
    s.Loop: st.check_loop,
    s.Match: st.check_match,
    s.Assert: st.check_assert,
    s.Break: st.check_jump,
    s.Continue: st.check_jump,
    s.Expand: st.check_expand,
    Hole: rs.check_hole,
    # -- expressions -----------------------------------------------------------------
    e.Lit: ex.check_lit,
    e.LocalRef: ex.check_local_ref,
    e.ConstRef: ex.check_const_ref,
    e.StorageRef: ex.check_storage_ref,
    e.GlobalRef: fv.check_global_ref,
    e.Field: fd.check_field,
    e.Index: ar.check_index,
    e.Slice: ar.check_slice,
    e.Call: ca.check_call,
    e.Unary: op.check_unary,
    e.Binary: op.check_binary,
    e.Cast: check_cast,
    e.Borrow: ca.check_borrow,
    e.Take: fd.check_take,
    e.Try: check_try,
    e.Lambda: lm.check_lambda,
    e.StructInit: ex.check_struct_init,
    e.EnumInit: rs.check_enum_init,
    e.TupleInit: fd.check_tuple_init,
    e.ArrayInit: ar.check_array_init,
    e.Range: rg.check_range,
    e.Interp: ca.check_interp,
}

PATTERN_ARMS: dict[type, Callable[..., Any]] = {
    p.Wildcard: mt.check_wildcard_pattern,
    p.Literal: mt.check_literal_pattern,
    p.IntRange: mt.check_range_pattern,
    p.Variant: mt.check_variant_pattern,
    p.Tuple: mt.check_tuple_pattern,
    p.Own: mt.check_own_pattern,
    p.Or: mt.check_or_pattern,
}
