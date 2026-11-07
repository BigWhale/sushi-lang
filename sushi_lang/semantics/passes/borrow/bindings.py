"""Registration and lifetime of the bindings a `match` arm or a `foreach` introduces.

A literal arm binds no name. A `Variant` and a `Tuple` bind the local of each bare
`PatBinding` in them (`bare_binders`), and each arm declares its binders as read-only views
of the payload or of the part (`declare_bare_binders`, docs/design/ir.md 7.9); a binder of a
payload that owns is a borrowed view of the scrutinee, with a borrow region. A `peek` or
`poke` binder is a reference into its payload, its part or the pointee of an `Own(...)`
(`reference_binders`; `references.py` declares it). A `nom` binder takes its payload
(`pattern_takes.py`).
"""

from __future__ import annotations
from typing import Iterable, Optional, TYPE_CHECKING

from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import Expr
from sushi_lang.semantics.ownership import TypeClass
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir.stmts import Bare, Ref
from sushi_lang.semantics.typesys import BorrowMode, Type

from .regions import note_binder
from .state import BorrowState, record_owners

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId
    from sushi_lang.semantics.shir.nodes import ShirBlock

    from . import BorrowChecker
    from .state import Owner


def release_binding_borrow(owner_state: Optional[BorrowState], binding: str) -> None:
    """Drop one binding from the list of live `let`-borrows out of an owner."""
    if owner_state is not None:
        owner_state.binding_borrows = [
            entry for entry in owner_state.binding_borrows if entry[0] != binding
        ]


def bare_binders(pattern: p.ShirPattern | p.PatBinding,
                 at: Optional[Span] = None) -> Optional[list[tuple[LocalId, Span]]]:
    """The bare binders of a pattern, each with the span of the variant name or of the tuple
    pattern that holds it, in the written order. An `Own(...)` binds the binders of its
    pointee, with the span of the pattern that holds the `Own`. A `peek` or `poke` binder is
    a reference (`reference_binders`), and a `nom` binder takes its payload
    (`pattern_takes.py`). None for a binder at the top of a pattern, which no arm writes.

    The alternatives of `|` bind one local for each name (CE2126), so the first alternative
    gives the binders.
    """
    if isinstance(pattern, (p.Wildcard, p.Literal, p.IntRange)):
        return []
    if isinstance(pattern, p.PatBinding):
        if not isinstance(pattern.by, Bare):
            return []
        if at is None:
            return None
        return [(pattern.local, at)]
    if isinstance(pattern, (p.Variant, p.Tuple)):
        items, holder = ((pattern.items, pattern.variant_loc) if isinstance(pattern, p.Variant)
                         else (pattern.elements, pattern.loc))
        found: list[tuple[LocalId, Span]] = []
        for item in items:
            inner = bare_binders(item, holder)
            if inner is None:
                return None
            found.extend(inner)
        return found
    if isinstance(pattern, p.Own):
        return bare_binders(pattern.inner, at)
    if isinstance(pattern, p.Or):
        alternatives = [bare_binders(item, at) for item in pattern.alternatives]
        if any(binders is None for binders in alternatives):
            return None
        return alternatives[0]
    return None


def declare_bare_binders(checker: 'BorrowChecker', binders: Iterable[tuple[LocalId, Span]],
                         owners: Optional[dict[LocalId, tuple[Owner, ...]]] = None,
                         body: Optional[ShirBlock] = None,
                         frame: Optional[list[tuple[Owner, Owner]]] = None) -> None:
    """Declare each binder of an arm as a read-only view of its payload (CE2414).

    The note of CE2414 points at the name of the variant or at the tuple pattern that binds
    it, as on `main`. A binder of a payload that owns views the storage of its root: it
    freezes the `owners` of the binder while it lives (CE2412), and it has a borrow region
    over the statements of `body`, the arm (docs/design/ir.md 7.9). `frame` holds the
    owners that the end of the arm releases.
    """
    for local, at in binders:
        ty = checker.local_type(local)
        state = BorrowState(name=checker.name_of(local), local=local, var_type=ty,
                            is_borrowed_binding=True, bound_at_span=at)
        checker.declare(state)
        if body is not None and checker.types.type_class(ty) is TypeClass.MOVE:
            record_owners(checker.borrow_state, state, (owners or {}).get(local, ()), at,
                          checker._scope_binding_borrows[-1] if frame is None else frame)
            note_binder(checker, state, body)


def reference_binders(pattern: p.ShirPattern | p.PatBinding
                      ) -> list[tuple[LocalId, BorrowMode, Optional[Span]]]:
    """Each `peek` or `poke` binder of a pattern, with its mode and the span of `poke x` of
    an `Own(poke x)` (None for any other binder), in the written order. The first
    alternative of `|` gives them (CE2126)."""
    if isinstance(pattern, p.PatBinding):
        return ([(pattern.local, pattern.by.mode, pattern.by.loc)]
                if isinstance(pattern.by, Ref) else [])
    if isinstance(pattern, p.Own):
        return reference_binders(pattern.inner)
    if isinstance(pattern, p.Variant):
        return [ref for item in pattern.items for ref in reference_binders(item)]
    if isinstance(pattern, p.Tuple):
        return [ref for item in pattern.elements for ref in reference_binders(item)]
    if isinstance(pattern, p.Or):
        return reference_binders(pattern.alternatives[0])
    return []


class BindingScope:
    """The lifetime of one `match` arm's or `foreach` loop's bindings (#337, #300).

    A binding lives for its arm and no longer, so it must give back whatever outer local
    it shadows; a REFERENCE binding additionally freezes the owner it points into for the
    same span. Both are undone on the way out, on every path -- the bracket used to be
    written by hand at each site, with the two accumulators threaded through five calls.
    """

    def __init__(self, checker: 'BorrowChecker') -> None:
        """Open a scope over `checker`'s borrow state."""
        self.checker = checker
        self._displaced: dict = {}
        self._frozen: list = []

    def __enter__(self) -> "BindingScope":
        """Enter the arm."""
        return self

    def __exit__(self, *exc) -> None:
        """Give back every shadowed local and release every owner freeze."""
        for name, previous in self._displaced.items():
            if previous is None:
                self.checker.borrow_state.pop(name, None)
            else:
                self.checker.borrow_state[name] = previous
        for owner, binding in self._frozen:
            release_binding_borrow(self.checker.borrow_state.get(owner), binding)
        return None

    def register(self, state: BorrowState) -> None:
        """Install a binding, saving whatever entry it shadows."""
        if state.name not in self._displaced:
            self._displaced[state.name] = self.checker.borrow_state.get(state.name)
        self.checker.borrow_state[state.name] = state

    def bind_item(self, name: str, ty: Optional[Type], span: Optional[Span]) -> None:
        """Bind the item a `next()` protocol iterator answered: the iteration OWNS it.

        The item is the payload of a fresh `Maybe@(T)` nobody else frees, so unlike a
        container element it aliases nothing, and the body may write it or hand it
        away -- which is what the `??` binder does, `let T x = <item>??` spending the
        item like any owned local (#548). The item is created per iteration, so a move
        at the top of the body reaches each exit of the item's scope and needs no
        run-time drop flag.
        """
        self.register(BorrowState(name=name, var_type=ty, declared_at_span=span))

    def bind_lent_walk(self, iterable: Expr) -> Optional[BorrowState]:
        """Freeze the owners of a lent iterator value that a `foreach` walks (#1228).

        A lent temporary (`xs.iter().map(f)`, `it.clone()`) reads the storage of its
        owners on every round, as a direct walk reads its container, so the loop is a
        `let`-borrow of each owner with a name no source can spell.
        """
        from .iterators import lent_owners
        owners = lent_owners(self.checker, iterable)
        if not owners:
            return None
        state = BorrowState(name=f"<iterator {id(iterable)}>", bound_at_span=iterable.loc)
        self.register(state)
        record_owners(self.checker.borrow_state, state, owners, iterable.loc, self._frozen)
        frozen = any(binding == state.name for _owner, binding in self._frozen)
        return state if frozen else None
