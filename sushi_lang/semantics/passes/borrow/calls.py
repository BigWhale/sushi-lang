"""Call sites: which declared mode each argument lands on, and what that mode does."""

from __future__ import annotations
from typing import Optional, Sequence, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    Borrow, Call, CallLike, DotCall, Expr, IndexAccess, MemberAccess, MethodCall,
    MethodLike, Name, Spread,
)
from sushi_lang.semantics.ownership import TypeClass
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.typesys import BorrowMode, FunctionType
from sushi_lang.semantics.param_modes import (
    CalleeKind, ParamMode, borrow_mode, effective_modes, receiver_mode,
)

from .borrows import register_implicit_borrow
from .consume import consume, consume_each
from .diagnostics import emit_use_of_invalidated_borrow, expr_to_string
from .methods import BULK_WRITE_METHODS, CONTAINER_INSERT_METHODS, effect_of
from .reads import OWNER_STEPS, called_on, read_type
from .state import BorrowState

if TYPE_CHECKING:
    from . import BorrowChecker


def is_enum_constructor(checker: 'BorrowChecker', expr: DotCall) -> bool:
    """Is this `X.Y(args)` an enum constructor rather than a method call?"""
    receiver = expr.receiver
    if not isinstance(receiver, Name):
        return False
    return receiver.id in checker.enum_names and receiver.id not in checker.borrow_state


def maybe_mark_container_insert(checker: 'BorrowChecker', expr: MethodLike) -> None:
    """`l.push(x)` / `m.insert(k, v)` takes ownership -- the CONTAINER_INSERT use."""
    receiver = called_on(expr, *CONTAINER_INSERT_METHODS)
    # `read_type` is the ONE walker for read-through-an-owner shapes. A narrower twin
    # here did not unwrap TryExpr, so `outer.get(0)??.push(5)` went unstamped and the
    # seam reported CE0129.
    if receiver is None or not checker.types.is_container(read_type(checker, receiver)):
        return
    consume_each(checker, expr.args)


def unchanged_borrowed_roots(checker: 'BorrowChecker', expr: CallLike) -> frozenset[str]:
    """The roots of a call's borrowed arguments that no change has invalidated yet.

    Taken at the call's ENTRY, before its receiver and arguments are walked, so that
    `reject_borrow_read_by_the_change` can tell an invalidation this call made from an
    older one.
    """
    roots = (_arg_root(checker, arg) for arg in _borrowed_args(checker, expr))
    return frozenset(name for name, state in roots
                     if name is not None and state is not None
                     and state.invalidated_at is None)


def reject_borrow_read_by_the_change(checker: 'BorrowChecker', expr: CallLike,
                                     unchanged: frozenset[str]) -> None:
    """CE2412: a borrowed argument is read DURING the call that changes its owner.

    Every argument of a call is live while the callee runs (#888, #920). The call changes
    an owner through its receiver, a `poke` argument or a `nom` argument, and each of
    them invalidates every binding that reads out of that owner. A binding that is also
    a borrowed argument of that call is used while the change runs, whatever the order
    of the arguments. A consumed argument has its own refusal (CE2411).
    """
    for arg in _borrowed_args(checker, expr):
        name, state = _arg_root(checker, arg)
        # Only the invalidation THIS call made: an older one is the ordinary later use,
        # and a read after the change in the walk was already reported and cleared.
        if state is None or name not in unchanged or state.invalidated_at is None:
            continue
        emit_use_of_invalidated_borrow(checker, name, arg.loc, state, by_the_change=True)


def _arg_root(checker: 'BorrowChecker', arg: Expr) -> tuple[Optional[str], Optional[BorrowState]]:
    """The local an argument reads out of, and its borrow state."""
    root = walk_place(arg, Step.MEMBER | Step.INDEX).name
    if root is None:
        return None, None
    return root.id, checker.borrow_state.get(root.id)


def _borrowed_args(checker: 'BorrowChecker', expr: CallLike) -> list[Expr]:
    """The arguments a call reads without taking them, `peek` ones unwrapped."""
    if isinstance(expr, Call):
        args = _borrowed_call_args(checker, expr)
    else:
        args = _borrowed_method_args(checker, expr)
    return [arg.expr if isinstance(arg, Borrow) else arg for arg in args
            if not (isinstance(arg, Borrow)
                    and borrow_mode(arg.mutability) is BorrowMode.POKE)]


def _borrowed_call_args(checker: 'BorrowChecker', expr: Call) -> list[Expr]:
    """A plain call's arguments that land on a parameter that does not consume."""
    if expr.callee_unresolved:
        return []
    kind, modes, variadic_at = call_modes(checker, expr)
    return [arg for i, arg in enumerate(expr.args)
            if (variadic_at is None or i < variadic_at)
            and not checker.callee_modes.mode_at(modes, i, kind).consumes]


def _borrowed_method_args(checker: 'BorrowChecker', expr: MethodLike) -> list[Expr]:
    """A method-shaped call's arguments that land on a parameter that does not consume."""
    modes = expr.callee_param_modes
    if modes is not None:
        return [arg for i, arg in enumerate(expr.args)
                if not checker.callee_modes.mode_at(modes, i, CalleeKind.METHOD).consumes]
    if (called_on(expr, *CONTAINER_INSERT_METHODS) is not None
            and checker.types.is_container(read_type(checker, expr.receiver))):
        return []
    return list(expr.args)


def reject_self_aliasing_copy(checker: 'BorrowChecker', expr: MethodLike) -> None:
    """CE2430: a bulk write may not read the array it is writing.

    Growing the destination may REALLOCATE its buffer, which leaves the source pointer
    dangling in the middle of the copy. The check compares PLACES rather than values,
    because `b.items.extend(b.items)` aliases exactly as `a.extend(a)` does. A refill
    (`a.fill(a[0])`) is the same fault one slot down; see `_reject_refill_from_own_slot`.
    """
    if effect_of(expr.method).refills:
        _reject_refill_from_own_slot(checker, expr)
        return
    receiver = called_on(expr, *BULK_WRITE_METHODS)
    if receiver is None or not expr.args:
        return
    place = _place_of(receiver)
    if place is None or place != _place_of(expr.args[0]):
        return
    er.emit(checker.reporter, er.ERR.CE2430, expr.args[0].loc, name=place, target=place)


def _reject_refill_from_own_slot(checker: 'BorrowChecker', expr: MethodLike) -> None:
    """CE2430: a refill may not read a slot of the array it refills, when the slot owns.

    The refill frees each slot before it stores the copy, so the slot that is the argument
    is freed and every later slot is copied from freed memory. A plain element is read by
    value and nothing aliases, so only an element type that owns a resource is refused.
    """
    if not expr.args or checker.types.type_class(checker.types.element_type(
            read_type(checker, expr.receiver))) is not TypeClass.MOVE:
        return
    target = _slot_path(expr.receiver)
    source = _slot_path(expr.args[0])
    if target is None or source is None or source[:len(target)] != target:
        return
    er.emit(checker.reporter, er.ERR.CE2430, expr.args[0].loc,
            name=expr_to_string(expr.args[0]), target=expr_to_string(expr.receiver))


_ANY_SLOT = "[]"
_SLOT_GET_OUTS = frozenset({"get", "first", "last"})


def _slot_path(expr: Expr) -> Optional[tuple[str, ...]]:
    """The storage an expression names, root first, with every slot read as ANY slot.

    Two index steps may name one slot, so an index and an element get-out both read as
    `_ANY_SLOT`, and a place off a slot is judged as if it were off every slot.
    """
    walked = walk_place(expr, OWNER_STEPS,
                        crosses_call=lambda call: call.method in _SLOT_GET_OUTS)
    if walked.name is None:
        return None
    steps: list[str] = [walked.name.id]
    for step in reversed(walked.path):
        if isinstance(step, MemberAccess):
            steps.append(step.member)
        elif isinstance(step, (IndexAccess, MethodCall, DotCall)):
            steps.append(_ANY_SLOT)
    return tuple(steps)


def _place_of(expr: Expr) -> Optional[str]:
    """The storage an expression names, as a dotted path, or None when it names none.

    A call result, a literal or an index names no storage a second expression can share.
    """
    walked = walk_place(expr, Step.MEMBER)
    if walked.name is None:
        return None
    members = [step.member for step in reversed(walked.path)
               if isinstance(step, MemberAccess)]
    return ".".join([walked.name.id, *members])


def maybe_mark_own_alloc_move(checker: 'BorrowChecker', expr: MethodLike) -> None:
    """`Own.alloc(x)` takes ownership of `x` -- the OWN_ALLOC consuming use."""
    receiver = called_on(expr, "alloc")
    if not (isinstance(receiver, Name) and receiver.id == "Own"):
        return
    consume_each(checker, expr.args)


def call_modes(checker: 'BorrowChecker',
               expr: Call) -> tuple[CalleeKind, tuple[ParamMode, ...], Optional[int]]:
    """The callee's kind, its parameter modes, and where a `...T` slot starts."""
    if not isinstance(expr.callee, Name):
        fn_type = expr.callee_fn_type
        modes = fn_type.modes if isinstance(fn_type, FunctionType) else ()
        return CalleeKind.INDIRECT, effective_modes(modes, CalleeKind.INDIRECT), None

    name = expr.callee.id
    state = checker.borrow_state.get(name)
    local_type = state.var_type if state is not None else None
    kind, modes = checker.callee_modes.for_name(name, local_type)
    variadic_at = (None if kind is CalleeKind.INDIRECT
                   else checker.callee_modes.variadic_from(name))
    return kind, modes, variadic_at


def apply_mode(checker: 'BorrowChecker', call: CallLike, arg: Expr, index: int,
               modes: Sequence[ParamMode], kind: CalleeKind) -> None:
    """Apply ONE declared parameter mode to the argument that lands on it.

    THE rule, shared by a plain call and an extension/perk method, so the two cannot
    disagree about what an unmarked argument does (docs/design/borrow-model.md S5).
    """
    mode = checker.callee_modes.mode_at(modes, index, kind)
    check_nom_marker(checker, call, arg, index, mode, kind)
    if mode.consumes:
        consume(checker, arg)
    elif not mode.by_pointer:
        register_implicit_borrow(checker, arg)


def consume_call_args(checker: 'BorrowChecker', expr: Call) -> None:
    """Consume the arguments the callee's declared modes say it takes ownership of."""
    # No callee, no declared modes, nothing to say about the arguments. The resolver
    # answers BORROW for a name it does not carry, so judging the marker against it told
    # the user to drop a `nom` nobody declared -- and the callee may well declare one
    # (#467). This mirrors `settle_method_args`, which has always declined the same way.
    if expr.callee_unresolved:
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
    consume(checker, arg)


def settle_receiver(checker: 'BorrowChecker', expr: MethodLike) -> None:
    """A `nom self` receiver is a consuming use of what the method was called on.

    The mode is DECLARATION-only, so nothing at the call site says `nom` and the
    diagnostic has to name the method instead (ruling R27). The name is recorded on the
    state here, beside the span the move already records.
    """
    if not receiver_mode(expr.callee_self_mode).consumes:
        return
    receiver = expr.receiver
    consume(checker, receiver)
    if isinstance(receiver, Name):
        state = checker.borrow_state.get(receiver.id)
        if state is not None and state.is_moved:
            state.consumed_by_method = state.consumed_by_method or expr.method


def settle_method_args(checker: 'BorrowChecker', expr: MethodLike) -> None:
    """Apply the declared modes of an extension or perk method to its arguments."""
    modes = expr.callee_param_modes
    if modes is None:
        return
    for i, arg in enumerate(expr.args):
        apply_mode(checker, expr, arg, i, modes, CalleeKind.METHOD)


def settle_namespaced_args(checker: 'BorrowChecker', expr: DotCall) -> None:
    """A name written through a namespace follows the modes its KIND declares.

    A function's modes are stamped on the node by the typecheck pass. A STRUCT declares
    none: a constructor consumes by position, so the answer comes from the one resolver
    every callee kind asks, exactly as the bare `Vec(1, 2)` gets it.
    """
    ref = expr.namespace_ref
    if ref is not None and ref.kind == "struct":
        kind, struct_modes = checker.callee_modes.for_name(ref.name)
        for i, arg in enumerate(expr.args):
            apply_mode(checker, expr, arg, i, struct_modes, kind)
        return

    modes = expr.callee_param_modes
    if modes is None:
        return
    for i, arg in enumerate(expr.args):
        apply_mode(checker, expr, arg, i, modes, CalleeKind.FUNCTION)


def consume_indirect_args(checker: 'BorrowChecker', expr: DotCall) -> None:
    """An indirect call through a fn-typed field follows the fn type's declared modes."""
    # `apply_mode`, like every other call shape: a thinner arm here skipped the implicit
    # borrow of an unmarked argument, so `h.handler(arr, poke arr)` compiled clean and read
    # the buffer the `poke` had reallocated (#365).
    fn_type = expr.callee_fn_type
    assert isinstance(fn_type, FunctionType)
    modes = effective_modes(fn_type.modes, CalleeKind.INDIRECT)
    for i, arg in enumerate(expr.args):
        apply_mode(checker, expr, arg, i, modes, CalleeKind.INDIRECT)


def check_nom_marker(checker: 'BorrowChecker', call: CallLike, arg: Expr, index: int,
                     mode: ParamMode, kind: CalleeKind) -> None:
    """The `nom` marker must be written at the call site if and only if it is declared."""
    if kind in (CalleeKind.CONSTRUCTOR, CalleeKind.CONTAINER):
        return
    if arg.nom_marked == mode.consumes:
        return
    span = arg.nom_span or arg.loc
    name = param_name(checker, call, index) or f"#{index + 1}"
    help_text = ("the callee takes ownership here; write `nom` at the call site too, or "
                 "`nom <arg>.clone()` to keep your own value") if mode.consumes else (
                 "the callee only borrows this argument, so it stays yours after the "
                 "call; drop the `nom`")
    checker.err.emit_with(er.ERR.CE2427, span, name=name).help(help_text).emit()


def param_name(checker: 'BorrowChecker', call: CallLike, index: int) -> Optional[str]:
    """The declared name of parameter `index` of a call's callee, if it is known."""
    names = call.callee_param_names
    if names is not None:
        return names[index] if index < len(names) else None
    if not isinstance(call, Call) or not isinstance(call.callee, Name):
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
