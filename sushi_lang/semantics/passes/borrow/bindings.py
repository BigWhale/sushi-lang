"""Registration and lifetime of the bindings a `match` arm or a `foreach` introduces."""

from __future__ import annotations
from enum import Enum, auto
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import (
    DotCall, Expr, LiteralPattern, MethodCall, Name, NomBinding, OrPattern, Pattern,
    RangePattern, RefBinding, TuplePattern,
)
from sushi_lang.semantics.constant_borrow import READ_ONLY_MODE
from sushi_lang.semantics.ownership import TypeClass
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.typesys import ReferenceType, Type

from .diagnostics import expr_to_string
from .reads import borrow_owner, names_kept_storage, root_owner
from sushi_lang.semantics.param_modes import borrow_mode
from .state import BorrowState

if TYPE_CHECKING:
    from . import BorrowChecker

CONTAINER_ITERATORS = frozenset({"iter", "keys", "values", "entries", "pairs"})


def release_binding_borrow(owner_state: Optional[BorrowState], binding: str) -> None:
    """Drop one binding from the list of live `let`-borrows out of an owner."""
    if owner_state is not None:
        owner_state.binding_borrows = [
            entry for entry in owner_state.binding_borrows if entry[0] != binding
        ]


def walks_a_temporary(checker: 'BorrowChecker', iterable: Expr) -> bool:
    """Does a `foreach` walk an owned temporary that only the loop keeps (#1014)?

    The iterator and the item then view the temporary, so neither freezes the local
    that the temporary was copied from.
    """
    return (isinstance(iterable, (MethodCall, DotCall))
            and not names_kept_storage(checker, iterable.receiver))


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

    def frozen_bindings(self) -> frozenset:
        """The names of the bindings this scope has frozen an owner for."""
        return frozenset(binding for _owner, binding in self._frozen)

    def register(self, state: BorrowState) -> None:
        """Install a binding, saving whatever entry it shadows."""
        if state.name not in self._displaced:
            self._displaced[state.name] = self.checker.borrow_state.get(state.name)
        self.checker.borrow_state[state.name] = state

    def bind_value(self, name: str, ty: Optional[Type], span: Optional[Span]) -> None:
        """Bind an element or payload BY VALUE -- a read-only view (CE2414)."""
        self.register(BorrowState(name=name, var_type=ty,
                                  is_borrowed_binding=True, bound_at_span=span))

    def bind_owned(self, name: str, ty: Optional[Type], span: Optional[Span]) -> None:
        """Bind a payload the arm TAKES: `Variant(nom x)` (ruling R11).

        Not `is_borrowed_binding`: the arm owns this value, so it may be written and it
        may be given away. Its `declared_branch_depth` is the ARM's, so a move deeper
        than the arm still earns a run-time drop flag exactly as a local's does.
        """
        self.register(BorrowState(name=name, var_type=ty, declared_at_span=span,
                                  declared_branch_depth=self.checker.branch_depth))

    def bind_item(self, name: str, ty: Optional[Type], span: Optional[Span]) -> None:
        """Bind the item a `next()` protocol iterator answered: the iteration OWNS it.

        The item is the payload of a fresh `Maybe@(T)` nobody else frees, so unlike a
        container element it aliases nothing, and the body may write it or hand it
        away -- which is what the `??` binder does, `let T x = <item>??` spending the
        item like any owned local (#548). Its `declared_branch_depth` is the BODY's,
        one deeper than the loop statement: the item is created per iteration, so a
        move at the top of the body dominates the item's scope exit and needs no
        run-time drop flag.
        """
        self.register(BorrowState(name=name, var_type=ty, declared_at_span=span,
                                  declared_branch_depth=self.checker.branch_depth + 1))

    def bind_ref(self, name: str, ty: Optional[Type], marker: str,
                 span: Optional[Span], owner: Optional[Expr],
                 declared_at: Optional[Span] = None) -> None:
        """Bind BY REFERENCE (#300), and freeze the owner it points into."""
        # The state carries the full `ReferenceType`, which wires every rule in by
        # construction. NOT `is_borrowed_binding` -- that would put a `poke` binding in
        # the CE2414 row and reject the write the marker exists to allow.
        state = BorrowState(name=name, var_type=ReferenceType(ty, borrow_mode(marker)),
                            bound_at_span=span,
                            declared_at_span=declared_at or span)
        self.register(state)
        if owner is not None:
            self.freeze_owner(state, owner, span, poke_span=declared_at or span)

    def bind_iterator(self, iterable: Expr) -> Optional[BorrowState]:
        """Freeze the container a `foreach` walks, for the whole loop (#956).

        The iterator reads the container's storage on every round, whatever the item
        does, so it is a `let`-borrow of the container with a name no source can spell.
        A range and a `next()` protocol value own their state; the caller does not ask.
        An iterator over an owned temporary (`a.clone().iter()`) walks storage that the
        loop alone keeps, so it freezes nothing (#1014).
        """
        if not isinstance(iterable, (MethodCall, DotCall)) or iterable.args:
            return None
        if iterable.method not in CONTAINER_ITERATORS:
            return None
        if walks_a_temporary(self.checker, iterable):
            return None
        state = BorrowState(name=f"<iterator {id(iterable)}>",
                            bound_at_span=iterable.loc,
                            views_storage_of=iterable.receiver)
        self.register(state)
        self.freeze_owner(state, iterable, iterable.loc)
        frozen = any(binding == state.name for _owner, binding in self._frozen)
        return state if frozen else None

    def freeze_owner(self, state: BorrowState, source: Expr, span: Optional[Span],
                     poke_span: Optional[Span] = None) -> None:
        """Give a reference binding the owner freeze a `let`-borrow gets (#242)."""
        owner = borrow_owner(source)
        owner_state = self.checker.borrow_state.get(owner) if owner else None
        if owner_state is None:
            return
        if _pokes_through_a_peek(state, owner_state):
            _reject_poke_through_peek(self.checker, owner, owner_state,
                                      poke_span or span)
            return
        state.borrows_from = owner
        owner_state.binding_borrows.append((state.name, span))
        self._frozen.append((owner, state.name))


def _own_payload_receiver(expr: Expr) -> Optional[Expr]:
    """`o.get()` on an Own binds the PAYLOAD; the place it is a place of is `o`."""
    from sushi_lang.semantics.ast import DotCall, MethodCall
    if isinstance(expr, (MethodCall, DotCall)) and expr.method == "get" and not expr.args:
        return expr.receiver
    return None


def bind_let_reference(checker: 'BorrowChecker', stmt) -> None:
    """`let poke T x = <place>` / `let peek T x = <place>`: a checked borrow binding (#409).

    Block-scoped like a `let`-borrow and a REFERENCE like a `poke` pattern binding: the
    state carries the full `ReferenceType`, so the write gates answer by construction (a
    write through a `peek` binding is CE2408, consuming the binding is CE2411), and the
    owner is frozen while the binding lives (CE2412's machinery, released at block exit).
    Two rules are this binding's own: one `poke` binding of an owner at a time (CE2403),
    and a `peek` beside a live `poke`, or the reverse, is CE2407 -- the same two codes an
    argument list answers, at the binding instead of the statement.
    """
    from .expressions import check_expr
    from .writes import check_owner_not_borrowed, reject_readonly_write

    check_expr(checker, stmt.value)
    is_poke = stmt.ty.is_poke()
    place = _own_payload_receiver(stmt.value) or stmt.value
    owner = borrow_owner(place)
    state = BorrowState(name=stmt.name, var_type=stmt.ty, bound_at_span=stmt.loc,
                        declared_at_span=stmt.loc,
                        declared_branch_depth=checker.branch_depth)
    checker.borrow_state[stmt.name] = state

    owner_state = checker.borrow_state.get(owner) if owner else None
    if owner_state is None:
        # A constant (a `peek` binds it, #713; a `poke` was CE2400 upstream) or a
        # temporary (CE2404 upstream): no local owns it, so nothing is frozen.
        return

    if is_poke:
        if _pokes_through_a_peek(state, owner_state):
            _reject_poke_through_peek(checker, owner, owner_state, stmt.loc)
            return
        if reject_readonly_write(checker, owner, stmt.loc, "take a `poke` binding",
                                 receiver=place):
            return

    if reject_a_second_writer(checker, owner, owner_state, is_poke, stmt.loc):
        return

    if is_poke:
        # A writer invalidates every value binding read out of the owner, exactly as
        # `f(poke x)` does (#242).
        check_owner_not_borrowed(checker, owner, stmt.loc, "take `poke`", place=place)

    state.borrows_from = owner
    owner_state.binding_borrows.append((stmt.name, stmt.loc))
    checker._scope_binding_borrows[-1].append((owner, stmt.name))


def reject_a_second_writer(checker: 'BorrowChecker', owner: str, owner_state: BorrowState,
                           is_poke: bool, span: Optional[Span],
                           siblings: frozenset = frozenset()) -> bool:
    """Report a reference binding beside a live one that excludes it; True when reported.

    The same table as `acquire_borrow`: `poke` beside `poke` is CE2403, a mix is CE2407,
    and two `peek` bindings share. `siblings` are the bindings of the same pattern, which
    point into disjoint payload slots.
    """
    for bound_name, bound_span in list(owner_state.binding_borrows):
        bound = checker.borrow_state.get(bound_name)
        if bound_name in siblings or bound is None \
                or not isinstance(bound.var_type, ReferenceType):
            continue
        if bound.var_type.is_poke():
            code = er.ERR.CE2403 if is_poke else er.ERR.CE2407
        elif is_poke:
            code = er.ERR.CE2407
        else:
            continue
        diag = checker.err.emit_with(code, span, name=owner)
        if bound_span is not None:
            diag.note_at(f"'{bound_name}' binds it here", bound_span)
        diag.emit()
        return True
    return False


def _pokes_through_a_peek(state: BorrowState, owner_state: BorrowState) -> bool:
    """Would this `poke` binding write the caller's container through a `peek`?"""
    return (isinstance(state.var_type, ReferenceType) and state.var_type.is_poke()
            and isinstance(owner_state.var_type, ReferenceType)
            and owner_state.var_type.is_peek())


def _reject_poke_through_peek(checker: 'BorrowChecker', owner: str,
                              owner_state: BorrowState, span: Optional[Span]) -> None:
    """Report CE2408 for a `poke` element binding out of a read-only borrow."""
    diag = checker.err.emit_with(er.ERR.CE2408, span, name=owner)
    if owner_state.declared_at_span is not None:
        diag.note_at(f"'{owner}' is declared here as a read-only borrow",
                     owner_state.declared_at_span)
    diag.help("a `poke` element binding would write the caller's container through a "
              "read-only borrow; declare the parameter `poke` if the elements must be "
              "written, or drop the marker and bind the element by value")
    diag.emit()


class ScrutineeKind(Enum):
    """What a payload binding reads through, which decides what it may do."""

    BORROWED = auto()       # a match that only borrows its scrutinee
    OWNED = auto()          # a match that owns it: a temporary, or `match nom r:`
    OWN_PAYLOAD = auto()    # the heap cell of an `Own(...)` pattern


def register_pattern_bindings(checker: 'BorrowChecker', scope: BindingScope,
                              pattern: object,
                              scrutinee_type: Optional[Type] = None,
                              scrutinee: Optional[Expr] = None,
                              owns_scrutinee: bool = False) -> None:
    """Register the bindings of a pattern that reads `scrutinee`, WITH their types."""
    kind = ScrutineeKind.OWNED if owns_scrutinee else ScrutineeKind.BORROWED
    _register_item(checker, scope, pattern, scrutinee_type, scrutinee, kind, None)


def _sub_items(checker: 'BorrowChecker', item: object,
               ty: Optional[Type]) -> list[tuple[object, Optional[Type]]]:
    """The positions directly inside an enum or a tuple pattern, each with its type."""
    if isinstance(item, Pattern):
        types = checker.types.variant_payload_types(ty, item.variant_name)
        items = item.bindings
    elif isinstance(item, TuplePattern):
        types = checker.types.tuple_element_types(ty)
        items = item.elements
    else:
        return []
    return [(sub, types[index] if index < len(types) else None)
            for index, sub in enumerate(items)]


def _register_item(checker: 'BorrowChecker', scope: BindingScope, item: object,
                   ty: Optional[Type], scrutinee: Optional[Expr], kind: ScrutineeKind,
                   span: Optional[Span]) -> None:
    """Register one pattern position, read through a scrutinee of `kind`.

    `span` is the span of the pattern that holds the position, for a bare name.
    """
    match item:
        case "_" | LiteralPattern() | RangePattern():
            pass                      # a discard, a literal and a range bind nothing
        case str():
            scope.bind_value(item, ty, span)
            freeze_for_a_view(checker, scope, item, ty, span, scrutinee, kind)
        case NomBinding():
            # `Variant(nom x)` (ruling R11): the arm TAKES the payload, which it may only
            # do out of a scrutinee the match owns. A take out of an `Own(...)` cell never
            # reaches here: the AST builder refuses it.
            if kind is ScrutineeKind.BORROWED:
                _reject_take_from_a_borrow(checker, item, scrutinee)
            scope.bind_owned(item.name, ty, item.loc or span)
        case RefBinding():
            # `Variant(poke x)` (#300): a REFERENCE into the scrutinee's storage. The
            # scrutinee is frozen for the arm -- rebinding it would change the variant tag
            # under the pointer.
            _bind_payload_ref(checker, scope, item.name, ty, item.mode, item.loc or span,
                              scrutinee, kind)
        case Pattern() | TuplePattern():
            inner_span = (item.variant_name_span or item.loc
                          if isinstance(item, Pattern) else item.loc)
            for sub, sub_type in _sub_items(checker, item, ty):
                _register_item(checker, scope, sub, sub_type, scrutinee, kind, inner_span)
        case OrPattern():
            # Each alternative binds the same names with the same types and modes
            # (CE2126), so the first one registers them for the arm.
            _register_item(checker, scope, item.alternatives[0], ty, scrutinee, kind,
                           item.loc or span)
        case _:
            _register_own_pattern(checker, scope, item, ty, span, scrutinee)


def freeze_for_a_view(checker: 'BorrowChecker', scope: BindingScope, name: str,
                       ty: Optional[Type], span: Optional[Span],
                       scrutinee: Optional[Expr], kind: ScrutineeKind) -> None:
    """A bare binding of an owning payload or element views the owner's storage.

    The copy is shallow, so the owner is frozen for the arm or the loop exactly as for a
    `let`-borrow: a change to the owner while the binding is still used is CE2412 (#888,
    #919). A plain value is a copy, and a scrutinee the match owns has no other owner to
    change.
    """
    if kind is not ScrutineeKind.BORROWED or scrutinee is None:
        return
    if checker.types.type_class(ty) is not TypeClass.MOVE:
        return
    scope.freeze_owner(checker.borrow_state[name], scrutinee, span)


def _reject_take_from_a_borrow(checker: 'BorrowChecker', binding: NomBinding,
                               scrutinee: Optional[Expr]) -> None:
    """Report CE2432: a `nom` binding under a match that only borrows its scrutinee."""
    text = expr_to_string(scrutinee) if scrutinee is not None else "r"
    diag = checker.err.emit_with(er.ERR.CE2432, binding.loc, name=binding.name)
    owner = root_owner(scrutinee) if scrutinee is not None else None
    state = checker.borrow_state.get(owner) if owner is not None else None
    if state is not None and state.declared_at_span is not None:
        diag.note_at(f"'{owner}' owns this value and still frees it",
                     state.declared_at_span)
    if owner is None and scrutinee is not None:
        # The scrutinee reads through a temporary that no name owns. `match nom` cannot
        # hand it over, because it consumes a borrow (CE2411).
        diag.help("a temporary still owns this value and frees it at scope exit; drop "
                  "the marker and call `.clone()` on the binding to keep a copy")
    else:
        diag.help(f"hand the value to the match -- `match nom {text}:` -- and it may be "
                  f"taken here; drop the marker to read through the borrow instead")
    diag.emit()


def reject_partial_take(checker: 'BorrowChecker', pattern: object,
                        scrutinee_type: Optional[Type]) -> None:
    """Report CE2433: an arm that takes one owning payload must take them all.

    All-or-nothing per arm and per scrutinee, for the reason the code's text states:
    what suppresses the match's free is the WHOLE scrutinee, so a payload left behind in
    a taking arm is freed by nobody. Each element of a tuple-literal scrutinee is its own
    scrutinee (ruling 3 of the tuple design), and the caller asks once for each. With `|`
    alternatives, each path through them is one way the arm can match, and each path
    takes the rule.
    """
    for taken, left in _survey_paths(checker, pattern, scrutinee_type, None):
        if taken and left:
            _report_partial_take(checker, taken, left)
            return


def _report_partial_take(checker: 'BorrowChecker', taken: tuple, left: tuple) -> None:
    """CE2433 at the first owning position a taking path leaves behind."""
    name, span = left[0]
    diag = checker.err.emit_with(er.ERR.CE2433, span, name=name)
    taken_name, taken_span = taken[0]
    if taken_span is not None:
        diag.note_at(f"'{taken_name}' is taken here, which moves the whole variant",
                     taken_span)
    diag.help(f"mark '{name}' `nom` as well, or drop the `nom` from '{taken_name}' and "
              f"read both through the borrow")
    diag.emit()


# One way an arm can match: the bindings it takes, and the owning positions it leaves.
_Path = tuple[tuple, tuple]


def _survey_paths(checker: 'BorrowChecker', item: object, ty: Optional[Type],
                  span: Optional[Span]) -> list[_Path]:
    """The taking bindings and the OWNING positions left behind, for each path.

    A pattern with no `|` alternatives has one path. Two paths that take and leave the
    same positions are one path.
    """
    if isinstance(item, OrPattern):
        return _unique([path for alternative in item.alternatives
                        for path in _survey_paths(checker, alternative, ty, span)])
    if isinstance(item, (Pattern, TuplePattern)):
        inner_span = (item.variant_name_span or item.loc
                      if isinstance(item, Pattern) else item.loc)
        paths: list[_Path] = [((), ())]
        for sub, sub_type in _sub_items(checker, item, ty):
            paths = _unique([(taken + sub_taken, left + sub_left)
                             for taken, left in paths
                             for sub_taken, sub_left in _survey_paths(
                                 checker, sub, sub_type, inner_span)])
        return paths
    if isinstance(item, NomBinding):
        return [(((item.name, item.loc or span),), ())]
    if isinstance(item, (LiteralPattern, RangePattern)) or span is None:
        return [((), ())]
    # Everything else keeps the payload where it is: a bare binding, a `poke` / `peek`
    # reference into it, an `Own(...)` unwrap, and a `_` discard, which is the same slot
    # with no name.
    if checker.types.type_class(ty) is not TypeClass.MOVE:
        return [((), ())]
    name = getattr(item, "name", None) or (item if isinstance(item, str) else "_")
    return [((), ((name, getattr(item, "loc", None) or span),))]


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


def _register_own_pattern(checker: 'BorrowChecker', scope: BindingScope, binding,
                          payload_type: Optional[Type], span: Optional[Span],
                          scrutinee: Optional[Expr]) -> None:
    """Register the inner binding of an `Own(...)` pattern."""
    inner = getattr(binding, "inner_pattern", None)
    inner_borrow = getattr(binding, "inner_borrow", None)
    pointee = checker.types.own_payload(payload_type)

    if isinstance(inner, (Pattern, TuplePattern, OrPattern, LiteralPattern, RangePattern)):
        _register_item(checker, scope, inner, pointee, scrutinee,
                       ScrutineeKind.OWN_PAYLOAD, span)
    elif isinstance(inner, str) and inner != "_":
        if inner_borrow is None:
            scope.bind_value(inner, pointee, span)
        else:
            # `Own(poke x)` (#300): a REFERENCE to the pointee, and the owner is frozen
            # for the arm like a `let`-borrow's (#242).
            borrow_span = getattr(binding, "inner_borrow_span", None) or span
            _bind_payload_ref(checker, scope, inner, pointee, inner_borrow,
                              borrow_span, scrutinee, ScrutineeKind.OWN_PAYLOAD)


def _bind_payload_ref(checker: 'BorrowChecker', scope: BindingScope, name: str,
                      ty: Optional[Type], marker: str, span: Optional[Span],
                      scrutinee: Optional[Expr], kind: ScrutineeKind) -> None:
    """Bind a reference into a matched payload, rejecting a scrutinee with no storage.

    Under a borrowed match the scrutinee must be a PLACE, the places a `let peek` /
    `let poke` takes (#788): the pointer aims into its storage, and the owner, the
    root of the place, is frozen for the arm. A temporary the match owns is parked in
    a slot for the whole statement (ruling R11), and an `Own(...)` pointee lives in
    its heap cell, so neither needs a place.
    """
    if kind is ScrutineeKind.BORROWED and scrutinee is not None \
            and not _is_a_place(scrutinee):
        scope.bind_ref(name, ty, marker, span, owner=None, declared_at=span)
        checker.err.emit(er.ERR.CE2404, span, expr=expr_to_string(scrutinee))
        return
    owner = scrutinee if kind is ScrutineeKind.BORROWED or isinstance(scrutinee, Name) \
        else None
    if owner is not None and reject_a_second_reference(
            checker, owner, marker, span, siblings=scope.frozen_bindings()):
        owner = None
    scope.bind_ref(name, ty, marker, span, owner=owner, declared_at=span)


def reject_a_second_reference(checker: 'BorrowChecker', place: Expr, marker: str,
                              span: Optional[Span],
                              siblings: frozenset = frozenset()) -> bool:
    """Report a new reference into `place` that a live reference of its root excludes.

    A pattern binding and a `foreach` item both ask this before they bind (#1027).
    """
    root = borrow_owner(place)
    owner_state = checker.borrow_state.get(root) if root else None
    if owner_state is None:
        return False
    return reject_a_second_writer(checker, root, owner_state, marker != READ_ONLY_MODE,
                                  span, siblings=siblings)


def _is_a_place(expr: Expr) -> bool:
    """Is `expr` a name, or a member or index chain off one?"""
    return walk_place(expr, Step.MEMBER | Step.INDEX).name is not None
