"""Expression shape probes, and the readers of what reads out of a live owner.

`read_type`, `borrow_owner` and `view_receiver` read a SHIR node: the type of the value of
a node, the owner (a local or a `var`) that a borrow of a place is counted on and that a
binding into the place freezes, and the receiver of a container get-out.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.method_effects import answers_view
from sushi_lang.semantics.generics.builtin_signatures import builtin_signature_of
from sushi_lang.semantics.ownership import TypeClass
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Builtin
from sushi_lang.semantics.shir.nodes import ShirNode
from sushi_lang.semantics.shir.typeck_check import misses_an_answer
from sushi_lang.semantics.typesys import Type, deref_type
from .state import BorrowState

if TYPE_CHECKING:
    from . import BorrowChecker
    from .state import Owner

# A read through an owner crosses every step: a field, an element, a call's receiver, a `??`.
OWNER_STEPS = Step.MEMBER | Step.INDEX | Step.CALL | Step.TRY


def borrow_owner(checker: 'BorrowChecker', node: object) -> Optional[Owner]:
    """The owner that a borrow of the SHIR place `node` is counted on, and that a binding
    into `node` freezes: the root local, or the symbol of the global of the root `var`,
    under each field step, each element and slice step, each `??` that reads it, and each
    call whose answer is a view of its receiver (`view_receiver`) or that reads its
    receiver through (`read_through_call`). None for a temporary.
    The pass tracks whole owners, not the places in them. A `Take` that is no field take
    is its place."""
    walked = _owner_walk(checker, node)
    if isinstance(walked.node, e.StorageRef):
        return storage_state(checker, walked.node).local
    return walked.local


def names_an_owner(checker: 'BorrowChecker', node: object) -> bool:
    """Whether the walk of `borrow_owner` ends at a name: a local, a constant or a `var`."""
    return isinstance(_owner_walk(checker, node).node, (e.LocalRef, e.ConstRef, e.StorageRef))


def _owner_walk(checker: 'BorrowChecker', node: object):
    if isinstance(node, e.Take):
        node = node.place
    return walk_place(node, Step.MEMBER | Step.INDEX | Step.TRY | Step.CALL,
                      crosses_call=lambda call: (view_receiver(checker, call) is not None
                                                 or read_through_call(checker, call)
                                                 is not None))


def view_receiver(checker: 'BorrowChecker', node: object) -> Optional[e.ShirExpr]:
    """The receiver of a call of a built-in method whose answer is a view of what the
    receiver holds (`answers_view`: a container get-out), or None for any other node."""
    if not isinstance(node, e.Call):
        return None
    resolved = checker.answers().callee_of(node.shir_id)
    callee = resolved.callee if resolved is not None else None
    if isinstance(callee, Builtin) and answers_view(callee.method):
        return callee.recv
    return None


def read_through_call(checker: 'BorrowChecker', node: object) -> Optional[e.ShirExpr]:
    """The receiver of a call of a built-in method that reads a borrowed receiver through
    (`reads_borrow_through`: `or_err`), when the receiver is a wrapper that owns, or None.

    The question is about the shape of the call and the type of its receiver. Who owns the
    receiver is the question of the caller (`consume.read_through_receiver`).
    """
    if not isinstance(node, e.Call):
        return None
    resolved = checker.answers().callee_of(node.shir_id)
    callee = resolved.callee if resolved is not None else None
    if not isinstance(callee, Builtin):
        return None
    receiver_type = deref_type(read_type(checker, callee.recv))
    signature = builtin_signature_of(receiver_type, callee.method.name)
    if (signature is None or not signature.reads_borrow_through
            or checker.types.type_class(receiver_type) is not TypeClass.MOVE):
        return None
    return callee.recv


def owner_state(checker: 'BorrowChecker', node: object) -> Optional[BorrowState]:
    """The state of the owner that a borrow of `node` is counted on (`borrow_owner`)."""
    owner = borrow_owner(checker, node)
    return checker.borrow_state.get(owner) if owner is not None else None


def storage_state(checker: 'BorrowChecker', node: e.StorageRef) -> BorrowState:
    """The borrow state of the `var` that `node` names, keyed by the symbol of its global.

    A `var` is no local of the body, so its state is made at its first borrow. It holds the
    two counters of the statement and the `let`-borrows out of it; a `var` is never moved
    out of (docs/design/unit-storage.md).
    """
    symbol = checker.answers().global_of(node.shir_id)
    if symbol is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail="a unit variable with no global reached the "
                                       "borrow pass")
    state = checker.borrow_state.get(symbol)
    if state is None:
        state = BorrowState(name=checker.decl_name(node.decl), local=symbol,
                            var_type=read_type(checker, node))
        checker.borrow_state[symbol] = state
    return state


def chain_call_boundary(expr: object) -> Optional[Span]:
    """The boundary that keeps a write receiver from reaching a root, or None (#352).

    A write receiver must reach its root -- the local, the `var` or the constant of a
    SHIR place -- through member and index steps only. Whatever else the walk stops at (a
    method call, a `??`, a plain call, an inline constructor) yields a temporary copy, so
    a write receiver whose chain crosses it cannot reach the storage it appears to write. Deliberately inverted --
    "a root is fine" rather than a list of boundary kinds -- so a new expression kind
    is rejected, never silently writable. The boundary's span is the second location
    of the CE2429 diagnostic. A constant is storage too: typecheck refuses a write of it
    (CE2096), also in a body that goes on to this pass with that error.
    """
    end = walk_place(expr, Step.MEMBER | Step.INDEX).node
    if end is None or isinstance(end, (e.LocalRef, e.StorageRef, e.ConstRef)):
        return None
    return end.loc


def slice_on_path(expr: object) -> Optional[e.Slice]:
    """The slice a write through `expr` crosses, or None (S4, docs/design/slices.md).

    A slice is a read-only view: a write through it is lost from no one, but a growth
    through it would move storage that the array keeps.
    """
    for node in walk_place(expr, Step.MEMBER | Step.INDEX).path:
        if isinstance(node, e.Slice):
            return node
    return None


def unknown(checker: 'BorrowChecker', node: object) -> bool:
    """Whether `node` is unknown: an error hole, or a node with no answer that it needs, in
    a body whose walk in typecheck reported an error (docs/design/ir.md 7.9)."""
    return (checker.typecheck_failed and isinstance(node, ShirNode)
            and misses_an_answer(checker.answers(), node))


def read_type(checker: 'BorrowChecker', node: object) -> Optional[Type]:
    """The type of the value that the SHIR node `node` makes or reads.

    A local has the type of its state, which keeps the reference type of a `peek` or a
    `poke` parameter. Every other node has the type that the typecheck pass answered: a
    `??` answers what it unwraps.
    """
    if isinstance(node, e.LocalRef):
        state = checker.borrow_state.get(node.local)
        return state.var_type if state is not None else checker.local_type(node.local)
    if isinstance(node, e.ExprNode):
        return checker.answers().type_of(node.shir_id)
    return None
