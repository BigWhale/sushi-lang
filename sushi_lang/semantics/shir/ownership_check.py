"""The completeness check of `OwnershipResults` (docs/design/ir.md 7.9, 8.10, ruling S6).

A consuming use is a value in a position that takes it over (`ConsumingUse`,
`semantics/ownership.py`). SHIR shows the position, so the rule reads the node and the
answers of `typecheck`:

- the value of an `Assign` (a `let`, a rebind, a write of a field or an element) and the
  value of a `Destructure`;
- the value of a `Return`;
- an argument of a `Call` whose convention is `Owned`, and a bloom argument;
- each field of a `StructInit`, each payload of an `EnumInit`, each part of a
  `TupleInit`, and the value of each `Single` run of an `ArrayInit` (a repeated value is
  a borrow, and each slot takes its own copy);
- the scrutinee of a `Match` that consumes (`match nom r:`);
- the operand of a `Try`, and the operand of every `Cast`: a conversion takes it as
  `nom self`, and an identity cast moves it (a plain operand gets ADOPT);
- the iterable of a `Loop` over an iterator value or a `next()` type that does not walk
  in place;
- each capture of a `Lambda`, from its environment layout in `LiftResults`.

The receiver of a method call is not on the list: `TypeckResults` has no convention row for
a receiver yet. The first group with method calls (A1) adds that row, and a `nom self`
receiver joins the rule.

Two writers of LENT have no check yet, because no spine body holds them: the clone of a
lent iterator (the iterators group, C3) and a borrowed call that answers an iterator (the
`peek` return group, B8). Each group adds its call to `_makes_an_iterator`.

When the `borrow` pass ends on a body with no error, the check walks the body once and
asks the table: each consuming use and each capture has an `Ownership`; each iterator
producer and each lazy adapter has LENT or OWNED, except the iterable of a direct walk
(`Cursor`, `HashMapCursor`), which makes no value; each lent one has its heap decision;
each `Loop` of kind `Protocol` or `IteratorValue` has its walk in place. The conditionally
moved locals have no check: no row for a local is the answer "moved on no path, or on
every path". REJECT is an error of `borrow`, except at two positions, where it is a
borrowed view with no error: the value of a `let` (a let-borrow of the owner) and the
operand of a `??` that reads through a borrowed owning wrapper. That operand is a local
that holds a borrow: a parameter with no `nom`, a local of a reference type, or a
let-borrow. A REJECT row at any other
position of a body that `borrow` ended with no error is a gap too. A gap is the internal
error CE0157, which names the pass and the table.
"""
from __future__ import annotations

from typing import Any, Callable, Iterator, TypeAlias

from sushi_lang.semantics.generics.iterators import is_lazy_adapter, produces_from_a_container
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.internals import errors as er
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import ShirBody
from sushi_lang.semantics.shir.callee import Builtin
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.lift import LiftResults
from sushi_lang.semantics.shir.loop_kind import Cursor, HashMapCursor, IteratorValue, Protocol
from sushi_lang.semantics.shir.ownership import OwnershipResults
from sushi_lang.semantics.shir.side_table import MissingAnswer, raise_first_gap
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.shir.walk import walk
from sushi_lang.semantics.typesys import ReferenceType

Consumed: TypeAlias = Callable[[Any, TypeckResults, OwnershipResults], Iterator[e.ShirExpr]]


def _value(node: s.Assign | s.Destructure | s.Return, _typeck: TypeckResults,
           _ownership: OwnershipResults) -> Iterator[e.ShirExpr]:
    yield node.value


def _call(node: e.Call, typeck: TypeckResults,
          _ownership: OwnershipResults) -> Iterator[e.ShirExpr]:
    for position, arg in enumerate(node.args):
        if arg.bloom or typeck.convention_of(node.shir_id, position) is Convention.OWNED:
            yield arg.value


def _fields(node: e.StructInit | e.EnumInit, _typeck: TypeckResults,
            _ownership: OwnershipResults) -> Iterator[e.ShirExpr]:
    yield from (arg.value for arg in (node.fields if isinstance(node, e.StructInit)
                                      else node.payload))


def _parts(node: e.TupleInit, _typeck: TypeckResults,
           _ownership: OwnershipResults) -> Iterator[e.ShirExpr]:
    yield from node.parts


def _runs(node: e.ArrayInit, _typeck: TypeckResults,
          _ownership: OwnershipResults) -> Iterator[e.ShirExpr]:
    yield from (run.value for run in node.runs if isinstance(run, e.Single))


def _scrutinee(node: s.Match, _typeck: TypeckResults,
               _ownership: OwnershipResults) -> Iterator[e.ShirExpr]:
    if node.consumes:
        yield node.scrutinee


def _try(node: e.Try, _typeck: TypeckResults,
         _ownership: OwnershipResults) -> Iterator[e.ShirExpr]:
    yield node.inner


def _cast(node: e.Cast, _typeck: TypeckResults,
          _ownership: OwnershipResults) -> Iterator[e.ShirExpr]:
    yield node.operand


def _iterable(node: s.Loop, typeck: TypeckResults,
              ownership: OwnershipResults) -> Iterator[e.ShirExpr]:
    if (isinstance(node.source, s.Items) and _walks_a_value(node, typeck)
            and ownership.in_place_of(node.shir_id) is False):
        yield node.source.iterable


#: The consuming positions of each node class.
CONSUMED: dict[type, Consumed] = {
    s.Assign: _value,
    s.Destructure: _value,
    s.Return: _value,
    e.Call: _call,
    e.StructInit: _fields,
    e.EnumInit: _fields,
    e.TupleInit: _parts,
    e.ArrayInit: _runs,
    s.Match: _scrutinee,
    e.Try: _try,
    e.Cast: _cast,
    s.Loop: _iterable,
}


def _walks_a_value(loop: s.Loop, typeck: TypeckResults) -> bool:
    """Is `loop` a walk of an iterator value or of a type that carries `next()`?"""
    return isinstance(typeck.loop_kind_of(loop.shir_id), (Protocol, IteratorValue))


def consuming_uses(body: ShirBody, typeck: TypeckResults,
                   ownership: OwnershipResults) -> list[e.ShirExpr]:
    """Each consuming use of `body` that is a node, in the order of the walk."""
    found: list[e.ShirExpr] = []
    for _path, value in walk(body.block, "block"):
        consumed = CONSUMED.get(type(value))
        if consumed is not None:
            found.extend(consumed(value, typeck, ownership))
    return found


def _direct_iterables(body: ShirBody, typeck: TypeckResults) -> set[ShirId]:
    """The iterable of each direct walk: it makes no iterator value, so it has no state."""
    return {value.source.iterable.shir_id for _path, value in walk(body.block, "block")
            if isinstance(value, s.Loop) and isinstance(value.source, s.Items)
            and isinstance(typeck.loop_kind_of(value.shir_id), (Cursor, HashMapCursor))}


def _makes_an_iterator(call: e.Call, typeck: TypeckResults) -> bool:
    """Is `call` an iterator producer over a container or a lazy adapter?"""
    resolved = typeck.callee_of(call.shir_id)
    if resolved is None or not isinstance(resolved.callee, Builtin):
        return False
    method = resolved.callee.method
    return (produces_from_a_container(method.family, method.name, len(call.args))
            or is_lazy_adapter(method.family, method.name))


def _iterator_gaps(call: e.Call, ownership: OwnershipResults) -> Iterator[str]:
    lent = ownership.lent_of(call.shir_id)
    if lent is None:
        yield "iterator state"
    elif lent and ownership.lent_on_heap(call.shir_id) is None:
        yield "heap decision"


def _capture_gaps(lam: e.Lambda, lift: LiftResults,
                  ownership: OwnershipResults) -> Iterator[str]:
    for capture in lift.environment_of(lam.shir_id) or ():
        if ownership.capture_ownership_of(lam.shir_id, capture.local) is None:
            yield f"ownership of capture _{capture.local.index}"


def _borrowed_locals(body: ShirBody, typeck: TypeckResults,
                     ownership: OwnershipResults) -> set[LocalId]:
    """The locals that hold a borrow: a parameter with no `nom`, a local of a reference
    type, and a let-borrow (a `let` whose value answers REJECT)."""
    borrowed = {param.local for param in body.params
                if param.convention is not Convention.OWNED}
    borrowed |= {local.id for local in body.locals
                 if isinstance(typeck.local_type(local.id), ReferenceType)}
    for _path, value in walk(body.block, "block"):
        if (isinstance(value, s.Assign) and value.is_init
                and isinstance(value.target, e.LocalRef)
                and ownership.ownership_of(value.value.shir_id) is Ownership.REJECT):
            borrowed.add(value.target.local)
    return borrowed


def borrowed_views(body: ShirBody, typeck: TypeckResults,
                   ownership: OwnershipResults) -> set[ShirId]:
    """The two positions where a REJECT row is a borrowed view and no error: the value of a
    `let`, and the operand of a `??` that reads through a borrowed wrapper. A REJECT row
    comes only for a wrapper that owns, so the operand is a local that holds a borrow, or a
    `??` over one (a nested `??`)."""
    borrowed = _borrowed_locals(body, typeck, ownership)
    views: set[ShirId] = set()
    for _path, value in walk(body.block, "block"):
        if isinstance(value, s.Assign) and value.is_init:
            views.add(value.value.shir_id)
        elif isinstance(value, e.Try) and walk_place(value.inner, Step.TRY).local in borrowed:
            views.add(value.inner.shir_id)
    return views


def missing_ownership(body: ShirBody, typeck: TypeckResults, lift: LiftResults,
                      ownership: OwnershipResults) -> list[MissingAnswer]:
    """Each answer that `body` needs and that `ownership` does not hold, in the order of the
    walk. `typeck` and `lift` give the positions: they are complete when `borrow` runs."""
    consumed = {node.shir_id for node in consuming_uses(body, typeck, ownership)}
    views = borrowed_views(body, typeck, ownership)
    direct = _direct_iterables(body, typeck)
    gaps: list[MissingAnswer] = []
    for _path, value in walk(body.block, "block"):
        if not isinstance(value, (e.ExprNode, s.Loop)):
            continue
        node = type(value).__name__
        answers: list[str] = []
        decided = ownership.ownership_of(value.shir_id)
        if value.shir_id in consumed and decided is None:
            answers.append("ownership")
        elif (value.shir_id in consumed and decided is Ownership.REJECT
                and value.shir_id not in views):
            answers.append("ownership other than reject")
        if (isinstance(value, e.Call) and value.shir_id not in direct
                and _makes_an_iterator(value, typeck)):
            answers.extend(_iterator_gaps(value, ownership))
        if isinstance(value, e.Lambda):
            answers.extend(_capture_gaps(value, lift, ownership))
        if (isinstance(value, s.Loop) and _walks_a_value(value, typeck)
                and ownership.in_place_of(value.shir_id) is None):
            answers.append("walk in place")
        gaps.extend(MissingAnswer(answer, node, value.loc) for answer in answers)
    return gaps


def check_ownership(body: ShirBody, typeck: TypeckResults, lift: LiftResults,
                    ownership: OwnershipResults, decls: DeclNames) -> None:
    """CE0157 at the first answer that `body` needs and that `ownership` does not hold."""
    raise_first_gap(er.ERR.CE0157, missing_ownership(body, typeck, lift, ownership),
                    decls.name_of(body.fn_id), writer="borrow", table=type(ownership).__name__)
