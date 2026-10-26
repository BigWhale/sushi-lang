"""The body walk of the borrow pass over SHIR (docs/design/ir.md 7.9).

ONE table, `ARMS`, gives the arm of each SHIR statement and expression class (gate:
`tests/unit/test_borrow_dispatch_is_total.py`). Every arm takes the pass and one node
(`check`). The lookup of an arm and the stop are the one dispatch of the SHIR passes
(`shir/dispatch.py`), which the typecheck pass reads too: a class with no row is an
internal error at the span of the node, and the arm of a construct that the pass does not
check yet is `stop_at`, which stops the walk of the body with the stop code CE0155.

`statements.py` walks a block and holds the statement arms, among them the branch, the
`while` loop, the `break` and the `continue`; `expressions.py` holds the expression arms,
and `calls.py` the arm of a call.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, NoReturn

from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import Hole

from . import calls as ca
from . import expressions as ex
from . import statements as st

if TYPE_CHECKING:
    from . import BorrowChecker

#: The name of this stage in the stop code.
STAGE = "borrow"


def stop_at(_checker: BorrowChecker, node: Any) -> NoReturn:
    """The arm of a construct that the pass does not check yet: the walk of the body stops."""
    dispatch.stop(STAGE, node)


def stop_at_family(node: Any, family: str) -> NoReturn:
    """The stop at a method call of a built-in family that the pass does not check yet."""
    dispatch.stop(STAGE, node, family=family)


def check(checker: BorrowChecker, node: Any) -> None:
    """Check one statement or one expression through its arm."""
    dispatch.arm_of(ARMS, node, STAGE)(checker, node)


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
    s.Break: st.check_break,
    s.Continue: st.check_continue,
    s.Expand: stop_at,
    Hole: stop_at,
    # -- expressions -----------------------------------------------------------------
    e.Lit: ex.check_leaf,
    e.LocalRef: ex.check_local_ref,
    e.ConstRef: ex.check_leaf,
    e.StorageRef: stop_at,
    e.GlobalRef: stop_at,
    e.Field: stop_at,
    e.Index: stop_at,
    e.Slice: stop_at,
    e.Call: ca.check_call,
    e.Unary: ex.check_unary,
    e.Binary: ex.check_binary,
    e.Cast: ex.check_cast,
    e.Borrow: ex.check_borrow,
    e.Take: stop_at,
    e.Try: ex.check_try,
    e.Lambda: stop_at,
    e.StructInit: stop_at,
    e.EnumInit: ex.check_enum_init,
    e.TupleInit: stop_at,
    e.ArrayInit: stop_at,
    e.Range: stop_at,
    e.Interp: ex.check_interp,
}
