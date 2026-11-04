"""SHIR to SLIR: the arms of a struct and a tuple, and the place of a field or an element (docs/design/ir.md 8.9).

A construction evaluates its fields in the written order (ruling R1), then builds ONE
`Aggregate` with the parts in the declaration order. A field is a `Field` projection of a
place, an element an `Index` projection and a slice a view (`lower_arrays.py`); a base that
is no place goes into a temporary of the statement first. A field write and an element
write store into the projection. A destructure gives one `Field` place for each target. Each arm
is a free function that takes the `Lowering` of `lower.py` first; the tables of `lower.py`
name them. This module reads `lower.py` for its types only, so no import cycle exists at
run time.

Plain values alone: a construction of a type that owns, a field of a temporary that owns and
a write of a field that owns stop (`StructInit owning`, `TupleInit owning`, `owning
temporary`, `owning field write`), because their drops and their moves are Tier B. `borrow`
stops on a destructure of a value that owns. A struct with a field of a reference type is
CE2415 at its declaration, so its construction stops (`StructInit reference field`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.nodes import ShirNode
from sushi_lang.semantics.slir import lower_arrays as arrays
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.function import STAGE
from sushi_lang.semantics.slir.lower_values import ShortCircuit, Value
from sushi_lang.semantics.typesys import ReferenceType, StructType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def struct_init(low: Lowering, node: e.StructInit) -> Value:
    """`Aggregate(Struct)` of the instance type, the fields in the written order."""
    ty = _instance(low, node)
    for arg in node.fields:
        if arg.bloom:
            raise StageStop(STAGE, "bloom", arg.value.loc)
    values = [arg.value for arg in node.fields]
    return r.Aggregate(r.AggStruct(ty), in_written_order(low, values, node.written_order))


def tuple_init(low: Lowering, node: e.TupleInit) -> Value:
    """`Aggregate(Tuple)` of the instance type, the parts in the written order."""
    ty = _instance(low, node)
    return r.Aggregate(r.AggTuple(ty), in_written_order(low, node.parts,
                                                         range(len(node.parts))))


def _instance(low: Lowering, node: e.StructInit | e.TupleInit) -> Type:
    ty = low.typeck.instance_of(node.shir_id)
    if ty is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"a {type(node).__name__} with no type reached SLIR")
    if low.owns(ty):
        dispatch.stop(STAGE, node, state="owning")
    if isinstance(ty, StructType) and any(isinstance(field_ty, ReferenceType)
                                          for _, field_ty in ty.fields):
        dispatch.stop(STAGE, node, state="reference field")
    return ty


def in_written_order(low: Lowering, values: Sequence[e.ShirExpr],
                      order: Sequence[int]) -> tuple[p.Operand, ...]:
    """The operand of each value, evaluated in `order`, in the declaration order. A place
    that a later value can change is copied into a temporary first."""
    operands: dict[int, p.Operand] = {}
    for at, index in enumerate(order):
        value = values[index]
        operand = low.operand(value)
        if isinstance(operand, p.Copy) and any(_calls(values[later]) for later in order[at + 1:]):
            operand = p.Copy(p.Local(low.temporary(r.Use(operand), low.type_of(value),
                                                   value.loc)))
        operands[index] = operand
    return tuple(operands[index] for index in range(len(values)))


def _calls(expr: e.ShirExpr) -> bool:
    """Whether a value can call a function, which can change a place through a `poke`. A
    literal, a name of a constant, a place, an operator and a construction over such values
    call none."""
    if isinstance(expr, (e.Lit, e.ConstRef, e.LocalRef, e.StorageRef)):
        return False
    if isinstance(expr, e.Field):
        return _calls(cast(e.ShirExpr, walk_place(expr, Step.MEMBER).node))
    if isinstance(expr, (e.Unary, e.Cast)):
        return _calls(expr.operand)
    if isinstance(expr, e.Binary):
        return _calls(expr.lhs) or _calls(expr.rhs)
    if isinstance(expr, e.TupleInit):
        return any(_calls(part) for part in expr.parts)
    if isinstance(expr, (e.StructInit, e.EnumInit)):
        args = expr.fields if isinstance(expr, e.StructInit) else expr.payload
        return any(_calls(arg.value) for arg in args)
    return True


def place_of(low: Lowering,
             expr: e.LocalRef | e.StorageRef | e.Field | e.Index | e.Slice) -> p.Place:
    """The place of a local, a `var`, a field, an element or a slice: one projection for
    each step of the walk to its root, from the root out. A root that is no place goes into
    a temporary of the statement; a temporary that owns stops at a field, and an array that
    owns is dropped at the end of the statement, after its element is read."""
    walked = walk_place(expr, Step.MEMBER | Step.INDEX)
    root: object = walked.node
    steps = [cast(e.Field | e.Index | e.Slice, step) for step in reversed(walked.path)]
    place: p.Place
    if isinstance(root, e.LocalRef):
        place = p.Local(low.local_of(root.local, root.loc))
    elif isinstance(root, e.StorageRef):
        place = low.global_place(root)
    elif isinstance(root, ShirNode) and steps:
        base = cast(e.ShirExpr, root)
        ty = low.type_of(base)
        if low.owns(ty) and isinstance(steps[0], e.Field):
            raise StageStop(STAGE, "owning temporary", steps[0].loc)
        place = p.Local(low.temporary(low.value(base), ty, base.loc))
    else:
        er.raise_internal_error("CE0000", span=expr.loc, detail="a field of no value")
    for step in steps:
        if isinstance(step, e.Index):
            place = arrays.element(low, place, step)
        elif isinstance(step, e.Slice):
            place = arrays.view(low, place, step)
        else:
            place = p.Field(place, _field_index(low, step))
    return place


def _field_index(low: Lowering, member: e.Field) -> int:
    index = low.typeck.field_index_of(member.shir_id)
    if index is None:
        er.raise_internal_error("CE0000", span=member.loc,
                                detail="a field with no index reached SLIR")
    return index


def field(low: Lowering, node: e.Field) -> Value:
    """A value that its statement stores at once, so a place in a `var` is read where it
    is."""
    return r.Use(low.operand(node, in_place=True))


def field_write(low: Lowering, stmt: s.Assign, target: e.Field | e.Index) -> None:
    """`p.x := v` and `a[i] := v`: the new value first, then the store into the projection.
    `and` and `or` go into a temporary first, because the right operand reads the place
    again. When the place holds an index, a value that is no constant goes into a
    temporary first too: the index and its check come after the whole value."""
    if low.owns(low.type_of(target)):
        raise StageStop(STAGE, "owning field write", stmt.loc)
    value = low.value(stmt.value)
    indexed = any(isinstance(step, (e.Index, e.Slice))
                  for step in walk_place(target, Step.MEMBER | Step.INDEX).path)
    if isinstance(value, ShortCircuit) or (indexed and not (
            isinstance(value, r.Use) and isinstance(value.operand, p.Const))):
        value = r.Use(p.Copy(p.Local(low.temporary(value, low.type_of(stmt.value),
                                                   stmt.value.loc))))
    low.write(place_of(low, target), value, stmt.loc)


def destructure(low: Lowering, stmt: s.Destructure) -> None:
    """One `Field` place for each target (ir.md 8.9). A `let` form reads a place of the
    value where it is; any other value, and the value of a rebind form, goes into a
    temporary of the statement first. Then each target from left to right: a binder is a
    new local of the block, and a place is rebound."""
    ty = low.type_of(stmt.value)
    if low.owns(ty):
        er.raise_internal_error("CE0000", span=stmt.loc,
                                detail="a destructure of a value that owns reached SLIR")
    if isinstance(stmt.value, (e.LocalRef, e.Field)) and not _rebinds(stmt.targets):
        operand = low.operand(stmt.value)
        if isinstance(operand, p.Const):
            er.raise_internal_error("CE0000", span=stmt.loc, detail="a place is a constant")
        source = operand.place
    else:
        source = p.Local(low.temporary(low.value(stmt.value), ty, stmt.value.loc))
    _bind(low, stmt.targets, source, stmt.loc)


def _rebinds(targets: Sequence[s.DestructureTarget]) -> bool:
    return any(isinstance(target, s.Place)
               or (isinstance(target, s.Nested) and _rebinds(target.targets))
               for target in targets)


def _bind(low: Lowering, targets: Sequence[s.DestructureTarget], source: p.Place,
          span: Span) -> None:
    for index, target in enumerate(targets):
        element = r.Use(p.Copy(p.Field(source, index)))
        if isinstance(target, s.Nested):
            _bind(low, target.targets, p.Field(source, index), span)
        elif isinstance(target, s.Place):
            if not isinstance(target.target, (e.LocalRef, e.StorageRef, e.Field, e.Index)):
                dispatch.stop(STAGE, target.target)
            low.write(place_of(low, target.target), element, span)
        elif isinstance(target, ShirLocalId):
            ident, ty = low.bind(target, span)
            if low.owns(ty):
                er.raise_internal_error("CE0000", span=span,
                                        detail="a binder that owns reached SLIR")
            low.placement.declare(ident, False)
            low.emit([st.StorageLive(ident, span)])
            low.write(p.Local(ident), element, span)
