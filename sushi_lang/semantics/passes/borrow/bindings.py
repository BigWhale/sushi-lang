"""Registration and lifetime of the bindings a `match` arm or a `foreach` introduces."""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import Expr, Name, Pattern, RefBinding
from sushi_lang.semantics.typesys import ReferenceType, Type

from .diagnostics import expr_to_string
from .reads import root_owner
from .state import BorrowState, borrow_mode

if TYPE_CHECKING:
    from . import BorrowChecker


def release_binding_borrow(owner_state: Optional[BorrowState], binding: str) -> None:
    """Drop one binding from the list of live `let`-borrows out of an owner."""
    if owner_state is not None:
        owner_state.binding_borrows = [
            entry for entry in owner_state.binding_borrows if entry[0] != binding
        ]


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

    def bind_value(self, name: str, ty: Optional[Type], span: Optional[Span]) -> None:
        """Bind an element or payload BY VALUE -- a read-only view (CE2414)."""
        self.register(BorrowState(name=name, var_type=ty,
                                  is_borrowed_binding=True, bound_at_span=span))

    def bind_ref(self, name: str, ty: Optional[Type], marker: Optional[str],
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

    def freeze_owner(self, state: BorrowState, source: Expr, span: Optional[Span],
                     poke_span: Optional[Span] = None) -> None:
        """Give a reference binding the owner freeze a `let`-borrow gets (#242)."""
        owner = root_owner(source)
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
        diag.note(f"'{owner}' is declared here as a read-only borrow",
                  owner_state.declared_at_span)
    diag.help("a `poke` element binding would write the caller's container through a "
              "read-only borrow; declare the parameter `poke` if the elements must be "
              "written, or drop the marker and bind the element by value")
    diag.emit()


def register_pattern_bindings(checker: 'BorrowChecker', scope: BindingScope,
                              pattern: Pattern,
                              scrutinee_type: Optional[Type] = None,
                              scrutinee: Optional[Expr] = None) -> None:
    """Register a match arm's payload bindings, WITH their types."""
    variant_types = checker.types.variant_payload_types(
        scrutinee_type, pattern.variant_name)
    span = pattern.variant_name_span or pattern.loc

    for index, binding in enumerate(pattern.bindings):
        payload_type = variant_types[index] if index < len(variant_types) else None
        match binding:
            case "_":
                pass                      # an explicit discard binds nothing
            case str():
                scope.bind_value(binding, payload_type, span)
            case RefBinding():
                # `Variant(poke x)` (#300): a REFERENCE into the scrutinee's payload. The
                # scrutinee is frozen for the arm -- rebinding it would change the
                # variant tag under the pointer.
                _bind_payload_ref(checker, scope, binding.name, payload_type,
                                  binding.mode, binding.loc or span, scrutinee)
            case Pattern():
                register_pattern_bindings(checker, scope, binding, payload_type,
                                          scrutinee=scrutinee)
            case _:
                _register_own_pattern(checker, scope, binding, payload_type, span,
                                      scrutinee)


def _register_own_pattern(checker: 'BorrowChecker', scope: BindingScope, binding,
                          payload_type: Optional[Type], span: Optional[Span],
                          scrutinee: Optional[Expr]) -> None:
    """Register the inner binding of an `Own(...)` pattern."""
    inner = getattr(binding, "inner_pattern", None)
    inner_borrow = getattr(binding, "inner_borrow", None)
    pointee = checker.types.own_payload(payload_type)

    if isinstance(inner, Pattern):
        register_pattern_bindings(checker, scope, inner, pointee, scrutinee=scrutinee)
    elif isinstance(inner, str) and inner != "_":
        if inner_borrow is None:
            scope.bind_value(inner, pointee, span)
        else:
            # `Own(poke x)` (#300): a REFERENCE to the pointee, and the owner is frozen
            # for the arm like a `let`-borrow's (#242).
            borrow_span = getattr(binding, "inner_borrow_span", None) or span
            _bind_payload_ref(checker, scope, inner, pointee, inner_borrow,
                              borrow_span, scrutinee, require_named_scrutinee=False)


def _bind_payload_ref(checker: 'BorrowChecker', scope: BindingScope, name: str,
                      ty: Optional[Type], marker: str, span: Optional[Span],
                      scrutinee: Optional[Expr],
                      require_named_scrutinee: bool = True) -> None:
    """Bind a reference into a matched payload, rejecting a scrutinee with no storage."""
    if require_named_scrutinee and scrutinee is not None \
            and not isinstance(scrutinee, Name):
        # The pointer aims INTO the scrutinee's storage, so the scrutinee must HAVE
        # storage. A temporary has none: the write would go nowhere.
        scope.bind_ref(name, ty, marker, span, owner=None, declared_at=span)
        checker.err.emit(er.ERR.CE2404, span, expr=expr_to_string(scrutinee))
        return
    scope.bind_ref(name, ty, marker, span, owner=scrutinee, declared_at=span)
