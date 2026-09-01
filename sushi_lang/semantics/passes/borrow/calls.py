"""Call sites: which declared mode each argument lands on, and what that mode does."""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import Borrow, Call, Expr, MemberAccess, Name, Spread
from sushi_lang.semantics.ownership import ConsumingUse
from sushi_lang.semantics.param_modes import CalleeKind, ParamMode, effective_modes

from .borrows import register_implicit_borrow
from .consume import consume, consume_each
from .reads import called_on, read_type

if TYPE_CHECKING:
    from . import BorrowChecker


# These store the argument and free it, so each is a consuming use. Only the METHOD NAME
# is matched loosely -- the receiver must be a container, so a user extension called
# `push` is not swept up.
CONTAINER_INSERT_METHODS = ("push", "insert")


def is_enum_constructor(checker: 'BorrowChecker', expr: Expr) -> bool:
    """Is this `X.Y(args)` an enum constructor rather than a method call?"""
    receiver = getattr(expr, "receiver", None)
    if not isinstance(receiver, Name):
        return False
    return receiver.id in checker.enum_names and receiver.id not in checker.borrow_state


def maybe_mark_container_insert(checker: 'BorrowChecker', expr: Expr) -> None:
    """`l.push(x)` / `m.insert(k, v)` takes ownership -- the CONTAINER_INSERT use."""
    receiver = called_on(expr, *CONTAINER_INSERT_METHODS)
    # `read_type` is the ONE walker for read-through-an-owner shapes. A narrower twin
    # here did not unwrap TryExpr, so `outer.get(0)??.push(5)` went unstamped and the
    # seam reported CE0129.
    if receiver is None or not checker.types.is_container(read_type(checker, receiver)):
        return
    consume_each(checker, expr.args, ConsumingUse.CONTAINER_INSERT)


# The bulk writes: a source they borrow, and a destination they grow.
_BULK_WRITE_METHODS = ("extend", "extend_range")


def reject_self_aliasing_copy(checker: 'BorrowChecker', expr: Expr) -> None:
    """CE2430: a bulk write may not read the array it is writing.

    Growing the destination may REALLOCATE its buffer, which leaves the source pointer
    dangling in the middle of the copy. The check compares PLACES rather than values,
    because `b.items.extend(b.items)` aliases exactly as `a.extend(a)` does.
    """
    receiver = called_on(expr, *_BULK_WRITE_METHODS)
    if receiver is None or not getattr(expr, "args", None):
        return
    place = _place_of(receiver)
    if place is None or place != _place_of(expr.args[0]):
        return
    er.emit(checker.reporter, er.ERR.CE2430, expr.args[0].loc, name=place)


def _place_of(expr: Expr) -> Optional[str]:
    """The storage an expression names, as a dotted path, or None when it names none.

    A call result, a literal or an index names no storage a second expression can share.
    """
    if isinstance(expr, Name):
        return expr.id
    if isinstance(expr, MemberAccess):
        base = _place_of(expr.receiver)
        return None if base is None else f"{base}.{expr.member}"
    return None


def maybe_mark_own_alloc_move(checker: 'BorrowChecker', expr: Expr) -> None:
    """`Own.alloc(x)` takes ownership of `x` -- the OWN_ALLOC consuming use."""
    receiver = called_on(expr, "alloc")
    if not (isinstance(receiver, Name) and receiver.id == "Own"):
        return
    consume_each(checker, expr.args, ConsumingUse.OWN_ALLOC)


def call_modes(checker: 'BorrowChecker',
               expr: Call) -> tuple[CalleeKind, tuple[ParamMode, ...], Optional[int]]:
    """The callee's kind, its parameter modes, and where a `...T` slot starts."""
    if not isinstance(expr.callee, Name):
        fn_type = getattr(expr, "callee_fn_type", None)
        modes = getattr(fn_type, "modes", ()) if fn_type is not None else ()
        return CalleeKind.INDIRECT, effective_modes(modes, CalleeKind.INDIRECT), None

    name = expr.callee.id
    state = checker.borrow_state.get(name)
    local_type = state.var_type if state is not None else None
    kind, modes = checker.callee_modes.for_name(name, local_type)
    variadic_at = (None if kind is CalleeKind.INDIRECT
                   else checker.callee_modes.variadic_from(name))
    return kind, modes, variadic_at


def apply_mode(checker: 'BorrowChecker', call, arg: Expr, index: int,
               modes, kind: CalleeKind) -> None:
    """Apply ONE declared parameter mode to the argument that lands on it.

    THE rule, shared by a plain call and an extension/perk method, so the two cannot
    disagree about what an unmarked argument does (docs/design/borrow-model.md S5).
    """
    mode = checker.callee_modes.mode_at(modes, index, kind)
    check_nom_marker(checker, call, arg, index, mode, kind)
    if mode.consumes:
        consume(checker, arg, ConsumingUse.CALL_ARG)
    elif not mode.by_pointer:
        register_implicit_borrow(checker, arg)


def consume_call_args(checker: 'BorrowChecker', expr: Call) -> None:
    """Consume the arguments the callee's declared modes say it takes ownership of."""
    # No callee, no declared modes, nothing to say about the arguments. The resolver
    # answers BORROW for a name it does not carry, so judging the marker against it told
    # the user to drop a `nom` nobody declared -- and the callee may well declare one
    # (#467). This mirrors `settle_method_args`, which has always declined the same way.
    if getattr(expr, "callee_unresolved", False):
        return
    kind, modes, variadic_at = call_modes(checker, expr)
    collected_owner_is_callee = (
        isinstance(expr.callee, Name)
        and checker.callee_modes.variadic_callee_owns(expr.callee.id))
    for i, arg in enumerate(expr.args):
        if variadic_at is not None and i >= variadic_at:
            _consume_collected(checker, arg, collected_owner_is_callee)
        else:
            apply_mode(checker, expr, arg, i, modes, kind)


def _consume_collected(checker: 'BorrowChecker', arg: Expr,
                       collected_owner_is_callee: bool) -> None:
    """A trailing argument that lands in a `...T` slot."""
    # A bloomed `arr...` hands the WHOLE array over, so it transfers only when something
    # else takes it. A collected element always transfers -- into the synthesized array,
    # whoever ends up owning that.
    if isinstance(arg, Spread) and not collected_owner_is_callee:
        return
    consume(checker, arg, ConsumingUse.ARRAY_ELEMENT)


def settle_receiver(checker: 'BorrowChecker', expr) -> None:
    """A `nom self` receiver is a consuming use of what the method was called on.

    The mode is DECLARATION-only, so nothing at the call site says `nom` and the
    diagnostic has to name the method instead (ruling R27). The name is recorded on the
    state here, beside the span the move already records.
    """
    from sushi_lang.semantics.param_modes import receiver_mode
    if not receiver_mode(getattr(expr, "callee_self_mode", None)).consumes:
        return
    receiver = getattr(expr, "receiver", None)
    if receiver is None:
        return
    consume(checker, receiver, ConsumingUse.RECEIVER)
    if isinstance(receiver, Name):
        state = checker.borrow_state.get(receiver.id)
        if state is not None and state.is_moved:
            state.consumed_by_method = state.consumed_by_method or expr.method


def settle_method_args(checker: 'BorrowChecker', expr) -> None:
    """Apply the declared modes of an extension or perk method to its arguments."""
    modes = getattr(expr, "callee_param_modes", None)
    if modes is None:
        return
    for i, arg in enumerate(expr.args):
        apply_mode(checker, expr, arg, i, modes, CalleeKind.METHOD)


def settle_namespaced_args(checker: 'BorrowChecker', expr) -> None:
    """A name written through a namespace follows the modes its KIND declares.

    A function's modes are stamped on the node by the typecheck pass. A STRUCT declares
    none: a constructor consumes by position, so the answer comes from the one resolver
    every callee kind asks, exactly as the bare `Vec(1, 2)` gets it.
    """
    ref = getattr(expr, "namespace_ref", None)
    if ref is not None and ref.kind == "struct":
        kind, modes = checker.callee_modes.for_name(ref.name)
        for i, arg in enumerate(expr.args):
            apply_mode(checker, expr, arg, i, modes, kind)
        return

    modes = getattr(expr, "callee_param_modes", None)
    if modes is None:
        return
    for i, arg in enumerate(expr.args):
        apply_mode(checker, expr, arg, i, modes, CalleeKind.FUNCTION)


def consume_indirect_args(checker: 'BorrowChecker', expr) -> None:
    """An indirect call through a fn-typed field follows the fn type's declared modes."""
    # `apply_mode`, like every other call shape: a thinner arm here skipped the implicit
    # borrow of an unmarked argument, so `h.handler(arr, poke arr)` compiled clean and read
    # the buffer the `poke` had reallocated (#365).
    modes = effective_modes(expr.callee_fn_type.modes, CalleeKind.INDIRECT)
    for i, arg in enumerate(expr.args):
        apply_mode(checker, expr, arg, i, modes, CalleeKind.INDIRECT)


def check_nom_marker(checker: 'BorrowChecker', call, arg: Expr, index: int,
                     mode: ParamMode, kind: CalleeKind) -> None:
    """The `nom` marker must be written at the call site if and only if it is declared."""
    if kind in (CalleeKind.CONSTRUCTOR, CalleeKind.CONTAINER):
        return
    marked = bool(getattr(arg, "nom_marked", False))
    if marked == mode.consumes:
        return
    span = getattr(arg, "nom_span", None) or arg.loc
    name = param_name(checker, call, index) or f"#{index + 1}"
    help_text = ("the callee takes ownership here; write `nom` at the call site too, or "
                 "`nom <arg>.clone()` to keep your own value") if mode.consumes else (
                 "the callee only borrows this argument, so it stays yours after the "
                 "call; drop the `nom`")
    checker.err.emit_with(er.ERR.CE2427, span, name=name).help(help_text).emit()


def param_name(checker: 'BorrowChecker', call, index: int) -> Optional[str]:
    """The declared name of parameter `index` of a call's callee, if it is known."""
    names = getattr(call, "callee_param_names", None)
    if names is not None:
        return names[index] if index < len(names) else None
    if not isinstance(getattr(call, "callee", None), Name):
        return None
    sig = checker.callee_modes.signature_of(call.callee.id)
    params = getattr(sig, "params", None) or ()
    return params[index].name if index < len(params) else None


def apply_destroy_effects(checker: 'BorrowChecker', call: Call) -> None:
    """Mark each argument the callee destroys through a `poke` parameter (#168)."""
    if not isinstance(call.callee, Name):
        return
    for index in checker.destroy_effects.get(call.callee.id, ()):
        if index >= len(call.args):
            continue
        arg = call.args[index]
        if isinstance(arg, Borrow):
            arg = arg.expr           # `poke map` -> `map`
        if isinstance(arg, Name) and arg.id in checker.borrow_state:
            checker.borrow_state[arg.id].is_destroyed = True
