"""The `nom` binders of a match arm: the arm takes the payload (docs/design/ir.md 7.8, 7.9).

A `nom` binder takes its payload out of the scrutinee, so the match must OWN the
scrutinee: a temporary, or `match nom r:` (`owns_scrutinee`). Under a match that only
borrows it, the binder is CE2432. An arm that takes one owning payload takes the variant
whole, so it must take every owning payload of the path (CE2433, `reject_partial_take`).
A `nom` binder is an owned local of the arm, with no region.
"""

from __future__ import annotations
from typing import Iterable, Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ownership import Provenance, TypeClass
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.typesys import Type

from .consume import source_provenance
from .diagnostics import spelled
from .reads import borrow_owner, names_an_owner
from .state import BorrowState

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId

    from . import BorrowChecker


def taken_binders(pattern: p.ShirPattern | p.PatBinding) -> list[LocalId]:
    """The `nom` binders of a pattern, in the written order. The alternatives of `|` bind
    one local for each name (CE2126), so the first alternative gives them."""
    if isinstance(pattern, p.PatBinding):
        return [pattern.local] if isinstance(pattern.by, p.Nom) else []
    if isinstance(pattern, p.Variant):
        return [local for item in pattern.items for local in taken_binders(item)]
    if isinstance(pattern, p.Tuple):
        return [local for item in pattern.elements for local in taken_binders(item)]
    if isinstance(pattern, p.Own):
        return taken_binders(pattern.inner)
    if isinstance(pattern, p.Or):
        return taken_binders(pattern.alternatives[0])
    return []


def owns_scrutinee(checker: 'BorrowChecker', node: s.Match, root: e.ShirExpr) -> bool:
    """May an arm TAKE a payload out of `root`? A temporary is owned by construction, and
    a place belongs to its owner unless the match says `nom` and takes it."""
    return node.consumes or source_provenance(checker, root) is Provenance.FRESH


def reject_take_from_a_borrow(checker: 'BorrowChecker', local: LocalId,
                              scrutinee: e.ShirExpr) -> None:
    """CE2432: a `nom` binder under a match that only borrows its scrutinee."""
    at = _loc(checker, local)
    diag = checker.err.emit_with(er.ERR.CE2432, at, name=checker.name_of(local))
    owner = borrow_owner(checker, scrutinee)
    state = checker.borrow_state.get(owner) if owner is not None else None
    if state is not None and state.declared_at_span is not None:
        diag.note_at(f"'{state.name}' owns this value and still frees it",
                     state.declared_at_span)
    if not names_an_owner(checker, scrutinee):
        # The scrutinee reads through a temporary that no name owns. `match nom` cannot
        # hand it over, because it consumes a borrow (CE2411).
        diag.help("a temporary still owns this value and frees it at scope exit; drop "
                  "the marker and call `.clone()` on the binding to keep a copy")
    else:
        diag.help(f"hand the value to the match -- `match nom {spelled(checker, scrutinee)}:` "
                  f"-- and it may be taken here; drop the marker to read through the borrow "
                  f"instead")
    diag.emit()


def reject_partial_take(checker: 'BorrowChecker', pattern: p.ShirPattern | p.PatBinding,
                        ty: Optional[Type]) -> None:
    """CE2433: an arm that takes one owning payload must take them all.

    All-or-nothing per arm and per scrutinee: what suppresses the free of the match is the
    WHOLE scrutinee, so a payload left behind in a taking arm is freed by nobody. Each part
    of a tuple-literal scrutinee is its own scrutinee (ruling 3 of tuples.md), and the
    caller asks once for each. With `|` alternatives, each path through them is one way the
    arm can match, and each path takes the rule.
    """
    for taken, left in _survey(checker, pattern, ty, None):
        if taken and left:
            name, span = left[0]
            diag = checker.err.emit_with(er.ERR.CE2433, span, name=name)
            taken_name, taken_span = taken[0]
            if taken_span is not None:
                diag.note_at(f"'{taken_name}' is taken here, which moves the whole variant",
                             taken_span)
            diag.help(f"mark '{name}' `nom` as well, or drop the `nom` from '{taken_name}' "
                      f"and read both through the borrow")
            diag.emit()
            return


def declare_taken_binders(checker: 'BorrowChecker', binders: Iterable[LocalId]) -> list[LocalId]:
    """Declare each `nom` binder of an arm as an owned local; the answer is the binders, for
    the locals of the block of the arm."""
    declared = []
    for local in binders:
        checker.declare(BorrowState(name=checker.name_of(local), local=local,
                                    var_type=checker.local_type(local),
                                    declared_at_span=_loc(checker, local)))
        declared.append(local)
    return declared


# One way an arm can match: the bindings it takes, and the owning positions it leaves.
_Path = tuple[tuple, tuple]


def _survey(checker: 'BorrowChecker', item: p.ShirPattern | p.PatBinding,
            ty: Optional[Type], span: Optional[Span]) -> list[_Path]:
    """The taking binders and the OWNING positions left behind, for each path. A pattern
    with no `|` alternatives has one path, and two paths that take and leave the same
    positions are one path."""
    if isinstance(item, p.Or):
        return _unique([path for alternative in item.alternatives
                        for path in _survey(checker, alternative, ty, span)])
    if isinstance(item, (p.Variant, p.Tuple)):
        if isinstance(item, p.Variant):
            types = checker.types.variant_payload_types(ty, item.variant)
            items, holder = item.items, item.variant_loc
        else:
            types = checker.types.tuple_element_types(ty)
            items, holder = item.elements, item.loc
        paths: list[_Path] = [((), ())]
        for index, sub in enumerate(items):
            sub_type = types[index] if index < len(types) else None
            paths = _unique([(taken + sub_taken, left + sub_left)
                             for taken, left in paths
                             for sub_taken, sub_left in _survey(checker, sub, sub_type,
                                                                holder)])
        return paths
    if isinstance(item, p.PatBinding) and isinstance(item.by, p.Nom):
        return [(((checker.name_of(item.local), _loc(checker, item.local)),), ())]
    if isinstance(item, (p.Literal, p.IntRange)) or span is None:
        return [((), ())]
    # Everything else keeps the payload where it is: a bare binder, a `peek` or `poke`
    # binder, an `Own(...)` unwrap, and a `_`, which is the same slot with no name.
    if checker.types.type_class(ty) is not TypeClass.MOVE:
        return [((), ())]
    if isinstance(item, p.PatBinding) and isinstance(item.by, s.Ref):
        return [((), ((checker.name_of(item.local), _loc(checker, item.local)),))]
    if isinstance(item, p.PatBinding):
        return [((), ((checker.name_of(item.local), span),))]
    return [((), (("_", item.loc if isinstance(item, p.Own) else span),))]


def _unique(paths: list[_Path]) -> list[_Path]:
    """The paths in order, each one time. Positions compare by name and by span."""
    seen: set = set()
    kept: list[_Path] = []
    for path in paths:
        key = tuple(tuple((name, id(where)) for name, where in half) for half in path)
        if key not in seen:
            seen.add(key)
            kept.append(path)
    return kept


def _loc(checker: 'BorrowChecker', local: LocalId) -> Optional[Span]:
    shir_local = checker.locals.get(local)
    return shir_local.loc if shir_local is not None else None
