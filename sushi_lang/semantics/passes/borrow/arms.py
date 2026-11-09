"""The body walk of the borrow pass over SHIR (docs/design/ir.md 7.9).

ONE table, `ARMS`, gives the arm of each SHIR statement and expression class (gate:
`tests/unit/test_borrow_dispatch_is_total.py`). Every arm takes the pass and one node
(`check`). The lookup of an arm is the one dispatch of the SHIR passes
(`shir/dispatch.py`), which the typecheck pass reads too: a class with no row is an
internal error at the span of the node, and so is a construct that an arm does not check
(`unhandled`).

`statements.py` walks a block and holds the statement arms, among them the branch, the
`match`, the `while` loop, the `break` and the `continue`; `expressions.py` holds the
expression arms, and `calls.py` the arm of a call.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, NoReturn

from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import Hole
from sushi_lang.semantics.shir.walk import parts

from . import calls as ca
from . import expressions as ex
from . import lambdas as lm
from . import statements as st
from .reads import unknown

if TYPE_CHECKING:
    from . import BorrowChecker

#: The name of this stage in an internal error.
STAGE = "borrow"


def unhandled(checker: BorrowChecker, node: Any) -> NoReturn:
    """The internal error of a construct that the pass does not check. After an error, the
    walk of the body ends there."""
    dispatch.unhandled(STAGE, node, after_an_error=checker.has_an_error())


def check(checker: BorrowChecker, node: Any) -> None:
    """Check one statement or one expression through its arm. An unknown node of a body with
    an error of typecheck has no arm (docs/design/ir.md 7.9): an unknown expression reads
    each expression that it holds, and an unknown statement declares its binders."""
    arm = dispatch.arm_of(ARMS, node, STAGE)
    if unknown(checker, node):
        if isinstance(node, s.Destructure):
            st.declare_binders(checker, node)
        elif isinstance(node, e.ExprNode):
            for part in parts(node):
                if isinstance(part, (e.ExprNode, Hole)) and not isinstance(part, e.GlobalRef):
                    check(checker, part)
        return
    arm(checker, node)


ARMS: dict[type, Callable[..., Any]] = {
    # -- statements ------------------------------------------------------------------
    s.Assign: st.check_assign,
    s.Destructure: st.check_destructure,
    s.ExprStmt: st.check_expr_stmt,
    s.Return: st.check_return,
    s.If: st.check_if,
    s.Loop: st.check_loop,
    s.Match: st.check_match,
    s.Assert: st.check_assert,
    s.Break: st.check_break,
    s.Continue: st.check_continue,
    s.Expand: st.check_expand,
    Hole: unhandled,
    # -- expressions -----------------------------------------------------------------
    e.Lit: ex.check_leaf,
    e.LocalRef: ex.check_local_ref,
    e.ConstRef: ex.check_leaf,
    e.StorageRef: ex.check_leaf,
    e.GlobalRef: ex.check_leaf,
    e.Field: ex.check_field,
    e.Index: ex.check_index,
    e.Slice: ex.check_slice,
    e.Call: ca.check_call,
    e.Unary: ex.check_unary,
    e.Binary: ex.check_binary,
    e.Cast: ex.check_cast,
    e.Borrow: ex.check_borrow,
    e.Take: ex.check_take,
    e.Try: ex.check_try,
    e.Lambda: lm.check_lambda,
    e.StructInit: ex.check_struct_init,
    e.EnumInit: ex.check_enum_init,
    e.TupleInit: ex.check_tuple_init,
    e.ArrayInit: ex.check_array_init,
    e.Range: ex.read_value,
    e.Interp: ex.check_interp,
}
