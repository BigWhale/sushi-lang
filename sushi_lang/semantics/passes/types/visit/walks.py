"""The walks of an iterator in the typecheck pass over SHIR (docs/design/ir.md 7.7).

A `foreach` whose iterable is a built-in producer over a container is a direct walk: `iter`
on a `T[]`, a `T[N]`, a `List@(T)` or a slice is a `Cursor`, and `keys`, `values`, `entries`
and `pairs` on a `HashMap@(K, V)` are a `HashMapCursor`. The iterable is no value. Its call
has the rows of any call of its family, and the kind of the loop says what it walks. Any
other `Iterator@(T)` is a value, and its loop is an `IteratorValue`. A type with a nullary
`next()` that answers `Maybe@(T)` is a `Protocol` walk, with the row of that `next()`. Any
other iterable is CE2033.

The binder is the item of the iterator. A written type that the item does not fit is CE2034.
A `peek` or a `poke` binder points into the storage of the container, and an entry, a
pair, an iterator value and the answer of a `next()` have none: that is CE2423 at the marker.
A `??` binder is a hidden binder and a `let` of its `??` at the top of the body, and an item
that is no `Result` is CE2517.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.iterators import produces_from_a_container
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.calls.methods import (
    RESOLUTION_REPORTED, _answer_from_template)
from sushi_lang.semantics.passes.types.compatibility import types_compatible
from sushi_lang.semantics.passes.types.resolution import resolve_variable_type
from sushi_lang.semantics.passes.types.statements import protocol_item_type
from sushi_lang.semantics.passes.types.utils import validate_type_name
from sushi_lang.semantics.passes.types.visit.iterator_methods import next_call
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.callee import Builtin
from sushi_lang.semantics.shir.loop_kind import (
    HASHMAP_VIEWS, Cursor, HashMapCursor, IteratorValue, LoopKind, Protocol)
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import IteratorType, Type, deref_type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

#: The producers whose item has storage in the container.
_ADDRESSABLE = frozenset({"iter", "keys", "values"})


def check_iterator_walk(validator: TypeValidator, node: s.Loop, source: s.Items) -> bool:
    """The iterable, the binder and the kind of a walk. True when the pass goes on with the
    body: a fault of the iterable or of the binder leaves the body unread."""
    iterable = source.iterable
    ty = visit.check_expr(validator, iterable)
    if ty is None:
        if not validator.reporter.has_errors:
            er.emit(validator.reporter, er.ERR.CE0146, iterable.loc)
        return False
    ty = deref_type(ty)
    walked = ((_kind_of(validator, iterable), ty.element_type) if isinstance(ty, IteratorType)
              else _protocol(validator, iterable, ty))
    if walked is None:
        return False
    kind, element = walked
    item = _binder_type(validator, source, element)
    if item is None:
        return False
    first = node.body.statements[0] if node.body.statements else None
    if isinstance(first, s.Assign) and unwraps_a_try_binder(validator, first) \
            and not is_instance_of(item, "Result"):
        end = first.value.loc
        marker = Span(end.end_line, end.end_col - len("??"), end.end_line, end.end_col)
        er.emit(validator.reporter, er.ERR.CE2517, marker, ty=display_type(item))
        return False
    if isinstance(source.by, s.Ref) and not _addressable(iterable, kind):
        er.emit(validator.reporter, er.ERR.CE2423, source.by.loc or node.loc)
        return False
    answers = validator.body_answers()
    if not isinstance(source.binder, s.Discard):
        answers.record_local(source.binder, item)
    answers.record_loop_kind(node.shir_id, kind)
    return True


def unwraps_a_try_binder(validator: TypeValidator, node: s.Assign) -> bool:
    """Whether a `let` is the one that the AST builder put at the top of the body of a `??`
    binder (`let x = <hidden>??`): the source writes no type for it, because `??` of the
    item gives it. A `peek`, a `poke` and a type on the binder are on the `let` or refused."""
    value = node.value
    return (isinstance(value, e.Try) and isinstance(value.inner, e.LocalRef)
            and validator.body_locals[value.inner.local].name is None)


def _addressable(iterable: e.ShirExpr, kind: LoopKind) -> bool:
    """Whether the item of the walk has storage in a container."""
    return (isinstance(kind, (Cursor, HashMapCursor)) and isinstance(iterable, e.Call)
            and isinstance(iterable.target, e.Dotted) and iterable.target.name in _ADDRESSABLE)


def _protocol(validator: TypeValidator, iterable: e.ShirExpr,
              ty: Type) -> Optional[tuple[LoopKind, Type]]:
    """The `Protocol` walk of a type with a nullary `next()` that answers `Maybe@(T)`, and
    its item. Any other type is CE2033, except a `next()` of a template whose bounds the type
    does not meet: that is its own fault, which the template rung reports (#1070)."""
    item = protocol_item_type(validator, ty)
    if item is not None:
        return Protocol(next_call(validator, iterable, ty, iterable.loc), item), item
    if _answer_from_template(validator, ty, "next", iterable.loc,
                             report=True) is not RESOLUTION_REPORTED:
        er.emit(validator.reporter, er.ERR.CE2033, iterable.loc, got=display_type(ty))
    return None


def _kind_of(validator: TypeValidator, iterable: e.ShirExpr) -> LoopKind:
    """The kind of the walk: a direct walk for a producer that the callee row of the call
    names, and an `IteratorValue` for any other iterator. An extension method can have the
    name of a producer."""
    resolved = (validator.body_answers().callee_of(iterable.shir_id)
                if isinstance(iterable, e.Call) else None)
    callee = resolved.callee if resolved is not None else None
    if not isinstance(callee, Builtin) or not produces_from_a_container(
            callee.method.family, callee.method.name, len(iterable.args)):
        return IteratorValue()
    view = HASHMAP_VIEWS.get(callee.method.name)
    return Cursor() if view is None else HashMapCursor(view)


def _binder_type(validator: TypeValidator, source: s.Items, item: Type) -> Optional[Type]:
    """The type of the binder: its written type when the item fits it, else the item. None
    after a fault of the written type."""
    if isinstance(source.binder, s.Discard):
        return item
    local = validator.body_locals[source.binder]
    if local.declared is None:
        return item
    declared = validator.written_type(local.declared)
    if validate_type_name(validator, declared, local.declared_loc):
        return None
    declared = resolve_variable_type(validator, declared, local.declared_loc)
    if not types_compatible(validator, declared, item):
        er.emit(validator.reporter, er.ERR.CE2034, local.declared_loc,
                expected=display_type(item), got=display_type(declared))
        return None
    return declared
