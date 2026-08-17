"""Registration and lifetime of the bindings a `match` arm or a `foreach` introduces."""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import Expr, Name, Pattern, RefBinding
from sushi_lang.semantics.typesys import BorrowMode, ReferenceType, Type

from .diagnostics import expr_to_string
from .reads import root_owner
from .state import BorrowState

if TYPE_CHECKING:
    from . import BorrowChecker


def register_binding(checker: 'BorrowChecker', name: str, state: BorrowState,
                     displaced: dict) -> None:
    """Install a pattern/foreach binding, saving whatever entry it shadows (#337)."""
    if name not in displaced:
        displaced[name] = checker.borrow_state.get(name)
    checker.borrow_state[name] = state


def restore_displaced(checker: 'BorrowChecker', displaced: dict) -> None:
    """End the bindings a `register_binding` bracket installed (#337)."""
    for name, previous in displaced.items():
        if previous is None:
            checker.borrow_state.pop(name, None)
        else:
            checker.borrow_state[name] = previous


def freeze_ref_binding_owner(checker: 'BorrowChecker', state: BorrowState, source: Expr,
                             span: Optional[Span], frozen: list,
                             poke_span: Optional[Span] = None) -> None:
    """Give a reference binding (#300) the owner freeze a `let`-borrow gets (#242)."""
    owner = root_owner(source)
    if owner is None:
        return
    owner_state = checker.borrow_state.get(owner)
    if owner_state is None:
        return
    if (isinstance(state.var_type, ReferenceType) and state.var_type.is_poke()
            and isinstance(owner_state.var_type, ReferenceType)
            and owner_state.var_type.is_peek()):
        diag = checker.err.emit_with(er.ERR.CE2408, poke_span or span, name=owner)
        if owner_state.declared_at_span is not None:
            diag.note(f"'{owner}' is declared here as a read-only borrow",
                      owner_state.declared_at_span)
        diag.help("a `poke` element binding would write the caller's container "
                  "through a read-only borrow; declare the parameter "
                  "`poke` if the elements must be written, or drop the marker "
                  "and bind the element by value")
        diag.emit()
        return
    state.borrows_from = owner
    owner_state.binding_borrows.append((state.name, span))
    frozen.append((owner, state.name))


def release_frozen(checker: 'BorrowChecker', frozen: list) -> None:
    """End the owner freezes `freeze_ref_binding_owner` installed."""
    for owner, binding in frozen:
        owner_state = checker.borrow_state.get(owner)
        if owner_state is not None:
            owner_state.binding_borrows = [
                entry for entry in owner_state.binding_borrows if entry[0] != binding
            ]


def register_pattern_bindings(checker: 'BorrowChecker', pattern: Pattern,
                              scrutinee_type: Optional[Type] = None,
                              displaced: Optional[dict] = None,
                              scrutinee: Optional[Expr] = None,
                              frozen: Optional[list] = None) -> None:
    """Register a match arm's payload bindings, WITH their types."""
    if displaced is None:
        displaced = {}
    variant_types = checker.types.variant_payload_types(
        scrutinee_type, pattern.variant_name)
    span = pattern.variant_name_span or pattern.loc

    for index, binding in enumerate(pattern.bindings):
        payload_type = variant_types[index] if index < len(variant_types) else None
        if isinstance(binding, str):
            if binding != "_":
                register_binding(checker, binding, BorrowState(
                    name=binding, var_type=payload_type,
                    is_borrowed_binding=True, bound_at_span=span,
                ), displaced)
        elif isinstance(binding, RefBinding):
            # `Variant(poke x)` (#300): a REFERENCE into the scrutinee's payload. The
            # scrutinee is frozen for the arm -- rebinding it would change the variant
            # tag under the pointer.
            mode = BorrowMode.POKE if binding.mode == "poke" else BorrowMode.PEEK
            ref_span = binding.loc or span
            state = BorrowState(
                name=binding.name,
                var_type=ReferenceType(payload_type, mode),
                bound_at_span=ref_span, declared_at_span=ref_span,
            )
            register_binding(checker, binding.name, state, displaced)
            if scrutinee is not None:
                # The pointer aims INTO the scrutinee's storage, so the scrutinee must
                # HAVE storage. A temporary has none: the write would go nowhere.
                if not isinstance(scrutinee, Name):
                    checker.err.emit(er.ERR.CE2404, ref_span,
                                     expr=expr_to_string(scrutinee))
                elif frozen is not None:
                    freeze_ref_binding_owner(
                        checker, state, scrutinee, ref_span, frozen, poke_span=ref_span)
        elif isinstance(binding, Pattern):
            register_pattern_bindings(checker, binding, payload_type, displaced,
                                      scrutinee=scrutinee, frozen=frozen)
        else:
            inner = getattr(binding, "inner_pattern", None)
            inner_borrow = getattr(binding, "inner_borrow", None)
            if isinstance(inner, Pattern):
                register_pattern_bindings(
                    checker, inner, checker.types.own_payload(payload_type), displaced,
                    scrutinee=scrutinee, frozen=frozen)
            elif isinstance(inner, str) and inner != "_":
                if inner_borrow is not None:
                    # `Own(poke x)` (#300): a REFERENCE to the pointee, and the
                    # owner is frozen for the arm like a `let`-borrow's (#242).
                    mode = (BorrowMode.POKE if inner_borrow == "poke"
                            else BorrowMode.PEEK)
                    borrow_span = getattr(binding, "inner_borrow_span", None) or span
                    state = BorrowState(
                        name=inner,
                        var_type=ReferenceType(
                            checker.types.own_payload(payload_type), mode),
                        bound_at_span=borrow_span, declared_at_span=borrow_span,
                    )
                    register_binding(checker, inner, state, displaced)
                    if scrutinee is not None and frozen is not None:
                        freeze_ref_binding_owner(
                            checker, state, scrutinee, borrow_span, frozen,
                            poke_span=borrow_span)
                else:
                    register_binding(checker, inner, BorrowState(
                        name=inner, var_type=checker.types.own_payload(payload_type),
                        is_borrowed_binding=True, bound_at_span=span,
                    ), displaced)
