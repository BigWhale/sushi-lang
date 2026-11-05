"""SHIR to SLIR: the one deep copy, and the reads of a `Result` or a `Maybe` that answer a
payload (docs/design/ir.md 8.7, 8.9).

`x.clone()` of every type is the rvalue `Clone`, and no call. `expect`, `realise` and `err`
are no call either: each is a switch on the tag of the receiver. The value that each one
reads out of its receiver comes from the shape of the receiver (`Source`): a plain value is
a `Copy`. An owning value of a place is a `Clone` of a copy of its projection. An owning
value of a temporary is a `Move` of the projection, which spends the temporary on that
path. An owning temporary drops at the read on each path that does not move out of it, as
the receiver of a `nom self` method. The default of `realise` has the same
shapes. Each arm is a free function that takes the `Lowering` of `lower.py` first. This
module reads `lower.py` for its types only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.method_effects import MAYBE_FAMILY, RESULT_FAMILY
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Builtin, BuiltinMethod
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import lower_control as ctl
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.ids import LocalId
from sushi_lang.semantics.slir.lower_values import Value, ViewValue
from sushi_lang.semantics.typesys import BuiltinType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Arrival, Block, Lowering

#: The variant of a `Result` or a `Maybe` whose payload `expect` and `realise` answer.
_SUCCESS = {RESULT_FAMILY: "Ok", MAYBE_FAMILY: "Some"}

#: The methods of a `Result` or a `Maybe` that read a payload with no call.
_READS = frozenset({"expect", "realise", "err"})

_PLACES = (e.LocalRef, e.StorageRef, e.Field, e.Index, e.Slice)


@dataclass(frozen=True, slots=True)
class Source:
    """A value that an arm reads from: a constant or a copy of a place, and whether the
    place is a temporary of the statement that a path can spend."""
    operand: p.Const | p.Copy
    spent: bool


@dataclass(frozen=True, slots=True)
class _Placed(Source):
    """A source in a place."""
    operand: p.Copy


def lowers_inline(method: BuiltinMethod) -> bool:
    """`clone` of any family, and `expect`, `realise` and `err` of a `Result` or a `Maybe`,
    are no call (ir.md 7.4, 8.9)."""
    return method.name == "clone" or (method.family in _SUCCESS and method.name in _READS)


def lower(low: Lowering, node: e.Call, callee: Builtin) -> Value:
    if callee.method.name == "clone":
        return clone(low, callee)
    if callee.method.name == "expect":
        return expect(low, node, callee)
    if callee.method.name == "realise":
        return realise(low, node, callee)
    return err(low, node, callee)


def clone(low: Lowering, callee: Builtin) -> Value:
    """`x.clone()` (ir.md 8.7): `Clone` of a copy of a place, of a constant and of a view.
    A temporary is the copy itself, so it gives its value and no `Clone`."""
    recv = callee.recv
    if isinstance(recv, (e.Lit, e.ConstRef, *_PLACES)):
        return r.Clone(source(low, recv).operand)
    value = low.value(recv)
    if not isinstance(value, ViewValue):
        return value
    return r.Clone(p.Copy(p.Local(low.temporary(value, low.type_of(recv), recv.loc))))


def expect(low: Lowering, node: e.Call, callee: Builtin) -> Value:
    """`expect(msg)` as an `assert` (ir.md 8.9): a switch on the tag of the receiver. The
    failing block evaluates the message and ends in the trap RE2029, which reads the position
    of the call and the message, and drops nothing. The next block reads the payload."""
    ty = low.type_of(callee.recv)
    receiver = _wrapper(low, callee.recv, ty)
    wrapper = receiver.operand.place
    success = _SUCCESS[callee.method.family]
    tag, payload = ctl.variant_of(ty, success, node.loc)
    found = _discriminant(low, wrapper, node.loc)
    fail, next_ = low.new_block(), low.new_block()
    low.jump(tm.SwitchInt(found, ((tag, next_.id),), fail.id, node.loc))
    (message,) = node.args
    ctl.trap_path(low, fail, next_, node.loc, lambda at: tm.Trap(
        "RE2029", (p.Const(k.text(at)), low.operand(message.value)), node.loc))
    return _read(low, receiver, p.Field(p.Downcast(wrapper, success), 0), payload[0])


def realise(low: Lowering, node: e.Call, callee: Builtin) -> Value:
    """`realise(default)`: the receiver, then the default, then a switch on the tag. The
    success path writes the payload, and the other path writes the default. A temporary
    drops on each path that does not take it."""
    ty, answer = low.type_of(callee.recv), low.type_of(node)
    receiver = _wrapper(low, callee.recv, ty)
    wrapper = receiver.operand.place
    (arg,) = node.args
    default = source(low, arg.value)
    success = _SUCCESS[callee.method.family]
    tag, payload = ctl.variant_of(ty, success, node.loc)
    takes = receiver.spent and low.owns(payload[0])
    result = _result(low, answer, node.loc)
    hit, other = _switch(low, wrapper, tag, node.loc)
    among, at_switch = low.placement.in_scope(), low.placement.state()
    low.block = hit
    low.write(p.Local(result),
              _read(low, receiver, p.Field(p.Downcast(wrapper, success), 0), payload[0]),
              node.loc)
    spent = [origin for origin, taken in ((default, False), (receiver, takes))
             if origin.spent and not taken]
    arrivals = [_leave(low, spent, node.loc)]
    low.placement.restore(at_switch)
    low.block = other
    low.write(p.Local(result), _read(low, default, None, answer), node.loc)
    arrivals.append(_leave(low, [receiver] if receiver.spent else [], node.loc))
    ctl.meet(low, low.new_block(), arrivals, among, node.loc)
    return _answer(low, result, answer)


def err(low: Lowering, node: e.Call, callee: Builtin) -> Value:
    """`err()` of a `Result`: `Some` of the error on the `Err` path, and `None` on the other
    path. A copy of the error is a temporary of its path, which the `Some` takes. A
    temporary drops on each path that does not take it."""
    ty, answer = low.type_of(callee.recv), low.type_of(node)
    receiver = _wrapper(low, callee.recv, ty)
    wrapper = receiver.operand.place
    tag, payload = ctl.variant_of(ty, "Err", node.loc)
    takes = receiver.spent and low.owns(payload[0])
    result = _result(low, answer, node.loc)
    hit, other = _switch(low, wrapper, tag, node.loc)
    among, at_switch = low.placement.in_scope(), low.placement.state()
    low.block = hit
    low.placement.open_statement()
    error = _operand(low, _read(low, receiver, p.Field(p.Downcast(wrapper, "Err"), 0),
                                payload[0]), payload[0], node.loc)
    low.write(p.Local(result), r.Aggregate(r.AggVariant(answer, "Some"), (error,)), node.loc)
    low.emit(low.placement.flush())
    low.emit(low.placement.close_scope(node.loc))
    arrivals = [_leave(low, [receiver] if receiver.spent and not takes else [], node.loc)]
    low.placement.restore(at_switch)
    low.block = other
    low.write(p.Local(result), r.Aggregate(r.AggVariant(answer, "None"), ()), node.loc)
    arrivals.append(_leave(low, [receiver] if receiver.spent else [], node.loc))
    ctl.meet(low, low.new_block(), arrivals, among, node.loc)
    return _answer(low, result, answer)


def source(low: Lowering, expr: e.ShirExpr) -> Source:
    """A constant, a place read where it is, or a new value in a temporary of the statement:
    a view goes into a view local, which owns nothing to spend."""
    if isinstance(expr, (e.Lit, e.ConstRef)):
        constant = low.operand(expr)
        if isinstance(constant, p.Const):
            return Source(constant, False)
    if isinstance(expr, _PLACES):
        return Source(p.Copy(agg.place_of(low, expr)), False)
    value, ty = low.value(expr), low.type_of(expr)
    temp = low.temporary(value, ty, expr.loc)
    return Source(p.Copy(p.Local(temp)), low.owns(ty) and not isinstance(value, ViewValue))


def _wrapper(low: Lowering, recv: e.ShirExpr, ty: Type) -> _Placed:
    """The source of a receiver, in a place: a constant goes into a temporary, because a
    `Discriminant` reads a place."""
    receiver = source(low, recv)
    if isinstance(receiver.operand, p.Const):
        return _Placed(p.Copy(p.Local(low.temporary(r.Use(receiver.operand), ty, recv.loc))),
                       False)
    return _Placed(receiver.operand, receiver.spent)


def _read(low: Lowering, origin: Source, projection: p.Place | None, ty: Type) -> r.Rvalue:
    """The value of type `ty` that a path reads from `origin`, at `projection` (None for
    the whole value): a constant, a `Copy` of a plain value, a `Move` out of a temporary that
    the path spends, and a `Clone` of a copy of any other owning value (ir.md 8.7)."""
    if isinstance(origin.operand, p.Const):
        return r.Use(origin.operand)
    place = origin.operand.place if projection is None else projection
    if not low.owns(ty):
        return r.Use(p.Copy(place))
    return r.Use(p.Move(place)) if origin.spent else r.Clone(p.Copy(place))


def _operand(low: Lowering, value: r.Rvalue, ty: Type, span: Span) -> p.Operand:
    """`value` as an operand: a `Clone` goes into a temporary of the statement first."""
    if isinstance(value, r.Use):
        return value.operand
    return p.Move(p.Local(low.temporary(value, ty, span)))


def _result(low: Lowering, ty: Type, span: Span) -> LocalId:
    """The temporary of the statement that each path writes."""
    ident = low.new_local(ty, None, None)
    low.placement.temporary(ident, low.owns(ty))
    low.emit([st.StorageLive(ident, span)])
    return ident


def _discriminant(low: Lowering, wrapper: p.Place, span: Span) -> p.Copy:
    found = low.new_local(BuiltinType.I32, None, None)
    low.emit([st.Assign(p.Local(found), r.Discriminant(wrapper), span)])
    return p.Copy(p.Local(found))


def _switch(low: Lowering, wrapper: p.Place, tag: int, span: Span) -> tuple[Block, Block]:
    """A switch on the tag of `wrapper`: to the first block on `tag`, else to the second."""
    found = _discriminant(low, wrapper, span)
    hit, other = low.new_block(), low.new_block()
    low.jump(tm.SwitchInt(found, ((tag, hit.id),), other.id, span))
    return hit, other


def _leave(low: Lowering, spent: list[Source], span: Span) -> Arrival:
    """The end of a path: the drop of each temporary that the other path takes, then the
    edge into the join."""
    for origin in spent:
        root = p.root_of(origin.operand.place) if isinstance(origin.operand, p.Copy) else None
        if not isinstance(root, p.Local):
            er.raise_internal_error("CE0000", span=span,
                                    detail="a path spends a value that is no temporary")
        low.emit(low.placement.drop_on_path(root.id, span))
    low.emit(low.placement.flush())
    return low.block, low.placement.state()


def _answer(low: Lowering, result: LocalId, ty: Type) -> r.Rvalue:
    return r.Use(p.Move(p.Local(result)) if low.owns(ty) else p.Copy(p.Local(result)))
