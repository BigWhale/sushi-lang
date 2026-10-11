"""The borrowed return `peek T` (#1229, docs/design/borrow-model.md S11).

A named function or method returns a view of a SOURCE. The source set is `self` when the
receiver is a borrow, and each borrowed parameter whose type owns a resource. The set is
judged where the declaration is WRITTEN, with each type parameter as a type that owns, so
each copy of a template has the same set. The record keeps the written parameters for
that reason: a copy shares the record of its template, and its own parameter types are
substituted.

Three readers, one answer: the typecheck pass (the declaration faults, and the source
positions that it stamps on a call), the borrow pass (the body check, and the owners that
a call borrows), and the backend (a call whose answer is a view gets no owner).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, FrozenSet, Optional, Sequence, Tuple

from sushi_lang.internals.report import Span
from sushi_lang.semantics.typesys import BuiltinType, ReferenceType, Type, UnknownType

# The name of the receiver in a source set, and its position in a call (`callee_borrows`).
RECEIVER = "self"
RECEIVER_POSITION = -1


@dataclass(frozen=True)
class BorrowedReturn:
    """A `peek` return as written: where, and the candidates for the source set.

    `candidates` holds each parameter that is not `nom`, with its written type. `refused`
    marks a position that the borrowed return is not valid in (a lambda, a perk method, a
    `nom self` method): the diagnostic was given, and no later rule judges it again.
    """
    span: Optional[Span]
    receiver: bool = False
    candidates: Tuple[Tuple[str, Optional[Type]], ...] = ()
    type_params: FrozenSet[str] = frozenset()
    refused: bool = False

    def refuse(self) -> 'BorrowedReturn':
        return replace(self, refused=True)


def record_of(span: Optional[Span], params: Sequence[Any], *, receiver: bool = False,
              type_params: Sequence[str] = ()) -> BorrowedReturn:
    """The record of a written `peek` return over `params` (each a `Param`)."""
    from sushi_lang.semantics.param_modes import param_mode
    candidates = tuple(
        (param.name, param.ty) for param in params
        if not param_mode(param).consumes
        and not getattr(param, "is_variadic", False) and not getattr(param, "is_pack", False))
    return BorrowedReturn(span=span, receiver=receiver, candidates=candidates,
                          type_params=frozenset(type_params))


def _as_owning(ty: Type, type_params: FrozenSet[str]) -> Type:
    """`ty` with each written type parameter as a type that owns (`string`)."""
    from sushi_lang.semantics.type_walk import map_named_types

    def stand_in(held: Type) -> Type:
        if isinstance(held, UnknownType) and held.namespace is None \
                and held.name in type_params:
            return BuiltinType.STRING
        return held

    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    mapped = map_named_types(ty, stand_in) if type_params else ty
    return mapped if mapped is not None else ty


def return_sources(record: BorrowedReturn, owns: Callable[[Type], bool]) -> Tuple[str, ...]:
    """The source set: `self` first, then each source parameter in declaration order."""
    if record.refused:
        return ()
    names = (RECEIVER,) if record.receiver else ()
    return names + tuple(name for name, ty in record.candidates
                         if ty is not None and owns(_as_owning(ty, record.type_params)))


def owner_in(tables: Any) -> Callable[[Type], bool]:
    """Does a type own a resource, over the program's tables: the one `owns` of this rule."""
    from sushi_lang.semantics.drop_set import drop_type_names
    from sushi_lang.semantics.generics.iterators import item_owner
    return item_owner(lambda: drop_type_names(tables.perk_impls),
                      tables.structs.by_name, tables.enums.by_name)


def sources_in(record: BorrowedReturn, tables: Any) -> Tuple[str, ...]:
    """The source set of `record`, judged over `tables`."""
    return return_sources(record, owner_in(tables))


def error_owns(record: BorrowedReturn, err_type: Optional[Type], tables: Any) -> bool:
    """Does the `E` of `peek T | E` own a resource? A type parameter counts as one."""
    if err_type is None or record.refused:
        return False
    return owner_in(tables)(_as_owning(err_type, record.type_params))


def source_positions(record: Optional[BorrowedReturn], param_names: Sequence[str],
                     tables: Any) -> Optional[Tuple[int, ...]]:
    """Where the sources stand in a call: an argument index, or RECEIVER_POSITION.

    None for a callable with an owned return. A refused record has no source, so its
    answer is a borrow of nothing.
    """
    if record is None:
        return None
    names = list(param_names)
    return tuple(RECEIVER_POSITION if source == RECEIVER else names.index(source)
                 for source in sources_in(record, tables)
                 if source == RECEIVER or source in names)


def borrowed_return_of(decl: Any) -> Optional[BorrowedReturn]:
    """The record a declaration, a signature or a lifted lambda carries, or None."""
    return getattr(decl, "borrowed_return", None)


def is_borrowed_call(expr: Any) -> bool:
    """Is `expr` a call of a callable with a borrowed return (a `??` over one included)?"""
    from sushi_lang.semantics.ast import TryExpr
    while isinstance(expr, TryExpr):
        expr = expr.expr
    return getattr(expr, "callee_borrows", None) is not None


def lends_a_view(expr: Any) -> bool:
    """Is `expr` the answer of a borrowed return that owns nothing of its own?

    The answer views storage that a source keeps, so nothing may free it. A returned
    iterator is the one exception: it owns its state, which its holder destroys.
    """
    from sushi_lang.semantics.ast import TryExpr
    from sushi_lang.semantics.generics.iterators import LENT
    while isinstance(expr, TryExpr):
        expr = expr.expr
    return (getattr(expr, "callee_borrows", None) is not None
            and getattr(expr, "iterator_state", None) != LENT)
