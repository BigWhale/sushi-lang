"""Call sites: which convention each argument has, and what that convention does.

`check_call` is the arm of a SHIR call of a free function, of `print`/`println`, of a
built-in method whose receiver the receiver partition reads, and of an extension method or
a static, with the conventions that the typecheck pass gave (docs/design/ir.md 8.3). The
other helpers read the AST of a built-in method call and of a call through a function
field: the groups that check those calls port them to SHIR (Phase 3 to 5).
"""

from __future__ import annotations
from typing import Optional, Sequence, TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    Call, CallLike, DotCall, Expr, IndexAccess, MemberAccess, MethodCall,
    MethodLike, Name,
)
from sushi_lang.semantics.method_effects import receiver_convention
from sushi_lang.semantics.ownership import Provenance, TypeClass
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.typesys import BorrowMode, FunctionType
from sushi_lang.semantics.param_modes import (
    CalleeKind, Convention, ParamMode, effective_modes, receiver_mode,
)
from sushi_lang.semantics.shir import dispatch, exprs as e
from sushi_lang.semantics.shir.callee import Builtin, Free, Intrinsic, Method, Static

from . import arms
from .borrows import (
    BY_ADDRESS, LATER_ROOTS, acquire_borrow, borrow_of_constant, check_borrow,
    register_implicit_borrow)
from .iterators import take_lent_iterator
from .consume import consume, consume_each, read_through_receiver, source_provenance
from .diagnostics import CopyUse, emit_use_of_invalidated_borrow, spelled
from .expressions import stop_at_a_field_take
from .methods import CONTAINER_INSERT_METHODS, callee_effect
from .reads import OWNER_STEPS, borrow_owner, called_on, read_type
from .writes import check_owner_not_borrowed, reject_readonly_write
from .state import BorrowState

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId

    from . import BorrowChecker


def maybe_mark_container_insert(checker: 'BorrowChecker', expr: MethodLike) -> None:
    """`l.push(x)` / `m.insert(k, v)` takes ownership -- the CONTAINER_INSERT use."""
    receiver = called_on(expr, *CONTAINER_INSERT_METHODS)
    # `read_type` is the ONE walker for read-through-an-owner shapes. A narrower twin
    # here did not unwrap TryExpr, so `outer.get(0)??.push(5)` went unstamped and the
    # seam reported CE0129.
    if receiver is None or not checker.types.is_container(read_type(checker, receiver)):
        return
    consume_each(checker, expr.args)


def check_call(checker: 'BorrowChecker', node: e.Call) -> None:
    """A call of a free function or of `print`/`println`, with the convention of each
    argument from the `TypeckResults` row of the call (docs/design/ir.md 8.3).

    Each argument is read first: a `peek` or a `poke` argument borrows its place there, as
    the marker is written, so a borrow and a consuming use of one value in one call
    conflict in both orders (CE2401). A `poke` argument for a `peek` parameter has the
    `InGuaranteed` convention of the parameter, and its borrow is still the `poke` that the
    source writes (CE2408 for a read-only place). Then each argument with no borrow takes
    its convention by POSITION (`apply_convention`): the callee owns an `Owned` argument,
    and the pass decides its `Ownership`. A callee that destroys its `poke` parameter
    destroys the caller's value (#168). The `nom` marker is compared against the declared
    mode by the typecheck pass (CE2427). A built-in method reads its receiver first. A
    family that has no arm yet stops the walk. The receiver of an extension method has the
    convention of its `self` (`check_method_receiver`): it is read after the call's entry,
    so a `poke self` change is a change of this call. A `poke self` receiver changes its
    owner, and a `nom self` receiver is taken, after the arguments, as an `Owned` argument
    is. The typecheck pass stops at every other
    callee, so one in this pass is an internal error.
    """
    resolved = checker.answers().callee_of(node.shir_id)
    callee = resolved.callee if resolved is not None else None
    if isinstance(callee, Builtin):
        _check_builtin_receiver(checker, node, callee)
    elif not isinstance(callee, (Free, Intrinsic, Method, Static)):
        er.raise_internal_error(
            "CE0000", span=node.loc,
            detail=f"the typecheck pass gave the borrow pass a call with the callee "
                   f"{type(callee).__name__}, and it stops at that call first")
    if any(arg.bloom for arg in node.args):
        arms.stop_at(checker, node)
    conventions = [_convention_of(checker, node, position)
                   for position in range(len(node.args))]
    unchanged = unchanged_borrowed_roots(checker, node, conventions)
    changes_receiver = (isinstance(callee, Method)
                        and check_method_receiver(checker, node, callee))
    modes = [_written_borrow(arg, convention)
             for arg, convention in zip(node.args, conventions, strict=True)]
    for arg, mode in zip(node.args, modes, strict=True):
        if mode is None:
            stop_at_a_field_take(checker, arg)
            arms.check(checker, arg.value)
        else:
            check_borrow(checker, arg.value, mode, arg.marker_loc or arg.value.loc)
    for arg, convention, mode in zip(node.args, conventions, modes, strict=True):
        if mode is None:
            apply_convention(checker, arg.value, convention)
    if isinstance(callee, Method) and callee.receiver is Convention.OWNED:
        take_receiver(checker, node, callee)
    if changes_receiver:
        _change_receiver_owner(checker, node, callee)
    reject_borrow_read_by_the_change(checker, node, conventions, unchanged)
    if isinstance(callee, Free):
        apply_destroy_effects(checker, node, callee)


def check_method_receiver(checker: 'BorrowChecker', node: e.Call, callee: Method) -> bool:
    """The receiver of an extension method, by the convention of its `self` (ir.md 8.3).

    The receiver is read first: a use after a move is CE2405. A plain `self` is the shared
    borrow of an unmarked argument, and a `peek self` a `peek` of its owner, for the
    statement. A `poke self` writes its receiver (`_write_receiver`). A `nom self`
    receiver is taken after the arguments (`take_receiver`). True when the call changes
    the owner of its receiver: a `poke self` that the pass did not refuse.
    """
    receiver = callee.recv
    arms.check(checker, receiver)
    if callee.receiver is Convention.GUARANTEED:
        register_implicit_borrow(checker, receiver)
    elif callee.receiver is Convention.IN_GUARANTEED:
        _borrow_owner_of(checker, receiver, is_poke=False)
    elif callee.receiver is Convention.INOUT:
        return _write_receiver(checker, node, callee)
    return False


def _write_receiver(checker: 'BorrowChecker', node: e.Call, callee: Method) -> bool:
    """A `poke self` receiver: the call writes the place of its receiver (#327).

    A `poke` of a constant is CE2400. A receiver that does not reach the storage of a
    local, or that reaches it through a read-only name, is refused by the one gate of a
    write (`reject_readonly_write`, CE2429 for a temporary). Else the receiver takes the
    exclusive borrow of its owner for the statement. True when the write is not refused.
    """
    receiver = callee.recv
    if borrow_of_constant(checker, receiver, BorrowMode.POKE):
        return False
    root = walk_place(receiver, Step.MEMBER).node
    if isinstance(root, LATER_ROOTS):
        arms.stop_at(checker, root)
    what = f"call `.{_method_name(checker, callee)}()`"
    owner = borrow_owner(receiver)
    if reject_readonly_write(checker, owner, node.loc, what, receiver=receiver):
        return False
    _borrow_owner_of(checker, receiver, is_poke=True)
    return True


def _change_receiver_owner(checker: 'BorrowChecker', node: e.Call, callee: Method) -> None:
    """The change of a `poke self` call: each `let`-borrow of the owner of the receiver is
    invalidated, after the arguments are read. A borrowed argument that reads one is read
    while the change runs (`reject_borrow_read_by_the_change`)."""
    what = f"call `.{_method_name(checker, callee)}()`"
    check_owner_not_borrowed(checker, borrow_owner(callee.recv), node.loc, what,
                             place=callee.recv)


def _borrow_owner_of(checker: 'BorrowChecker', receiver: e.ShirExpr, *,
                     is_poke: bool) -> None:
    """The borrow of the owner of a receiver for the statement, when the owner is a local
    that still holds its value. A receiver and an argument that reach one owner conflict
    as two arguments do (CE2403, CE2407)."""
    owner = borrow_owner(receiver)
    state = checker.borrow_state.get(owner) if owner is not None else None
    if state is not None and not state.is_moved:
        acquire_borrow(checker, state, receiver.loc, is_poke=is_poke)


def take_receiver(checker: 'BorrowChecker', node: e.Call, callee: Method) -> None:
    """A `nom self` receiver is a consuming use of what the method was called on.

    The mode is a fact of the declaration, so nothing at the call says `nom`, and the
    diagnostic names the method (ruling R27). The name is recorded on the state only when
    this call is the move: a receiver that an earlier move took keeps the facts of that
    move.
    """
    receiver = callee.recv
    method = _method_name(checker, callee)
    state = (checker.borrow_state.get(receiver.local)
             if isinstance(receiver, e.LocalRef) else None)
    already_moved = state is not None and state.is_moved
    arguments = _arguments_text(checker, [arg.value for arg in node.args])
    consume(checker, receiver, CopyUse(f"call '{method}' on the copy",
                                       f".{method}({arguments})"))
    if state is not None and state.is_moved and not already_moved:
        state.consumed_by_method = method


def _method_name(checker: 'BorrowChecker', callee: Method) -> str:
    """The name of the method that a `Method` callee names (`<type>.<method>`)."""
    return checker.decl_name(callee.decl).rpartition(".")[2]


def _check_builtin_receiver(checker: 'BorrowChecker', node: e.Call, callee: Builtin) -> None:
    """The receiver of a built-in method call: a read of its place.

    A `Guaranteed` receiver (`receiver_convention`, docs/design/ir.md 8.3) is read and
    stays the owner of its value. A family with no receiver rule stops the walk, and so
    does an `Owned` receiver: B10 checks the `nom self` methods (`map_err`, `or_err`),
    with the read-through of `or_err` and the method in the CE2405 note.
    """
    if receiver_convention(callee.method) is not Convention.GUARANTEED:
        dispatch.stop_at_family(arms.STAGE, node, callee.method.family)
    arms.check(checker, callee.recv)


def _written_borrow(arg: e.Arg, convention: Convention) -> Optional[BorrowMode]:
    """The mode of the borrow that an argument takes for the statement, or None when it
    takes none: its written `peek` or `poke` marker, else a by-address convention. A marked
    argument of `print` and `println` has the `Guaranteed` convention and borrows as it is
    written."""
    if arg.marker in BY_ADDRESS:
        return BY_ADDRESS[arg.marker]
    return BY_ADDRESS.get(convention)


def _convention_of(checker: 'BorrowChecker', node: e.Call, position: int) -> Convention:
    convention = checker.answers().convention_of(node.shir_id, position)
    if convention is None:
        er.raise_internal_error(
            "CE0000", span=node.loc,
            detail=f"the typecheck pass gave the argument {position + 1} of a call no "
                   f"convention")
    return convention


def apply_convention(checker: 'BorrowChecker', value: e.ShirExpr,
                     convention: Convention) -> None:
    """Apply the convention of ONE argument to its value.

    An `Owned` argument is a consuming use. A `Guaranteed` argument is the implicit
    shared borrow of an unmarked argument. An `InGuaranteed` and an `Inout` argument took
    their borrow when they were read.
    """
    if convention is Convention.OWNED:
        consume(checker, value)
    elif convention is Convention.GUARANTEED:
        register_implicit_borrow(checker, value)


def unchanged_borrowed_roots(checker: 'BorrowChecker', node: e.Call,
                             conventions: Sequence[Convention]) -> frozenset:
    """The roots of a call's borrowed arguments that no change has invalidated yet.

    Taken at the call's ENTRY, before its arguments are read, so that
    `reject_borrow_read_by_the_change` can tell an invalidation this call made from an
    older one.
    """
    roots = (_arg_root(checker, value) for value in _borrowed_args(node, conventions))
    return frozenset(local for local, state in roots
                     if local is not None and state is not None
                     and state.invalidated_at is None)


def reject_borrow_read_by_the_change(checker: 'BorrowChecker', node: e.Call,
                                     conventions: Sequence[Convention],
                                     unchanged: frozenset) -> None:
    """CE2412: a borrowed argument is read DURING the call that changes its owner.

    Every argument of a call is live while the callee runs (#888, #920). The call changes
    an owner through a `poke` argument or a `nom` argument, and each of them invalidates
    every binding that reads out of that owner. A binding that is also a borrowed argument
    of that call is used while the change runs, whatever the order of the arguments. A
    consumed argument has its own refusal (CE2411).
    """
    for value in _borrowed_args(node, conventions):
        local, state = _arg_root(checker, value)
        # Only the invalidation THIS call made: an older one is the ordinary later use,
        # and a read after the change in the walk was already reported and cleared.
        if state is None or local not in unchanged or state.invalidated_at is None:
            continue
        emit_use_of_invalidated_borrow(checker, state.name, value.loc, state,
                                       by_the_change=True)


def _arg_root(checker: 'BorrowChecker',
              value: e.ShirExpr) -> tuple[Optional[LocalId], Optional[BorrowState]]:
    """The local an argument reads out of, and its borrow state."""
    local = borrow_owner(value)
    if local is None:
        return None, None
    return local, checker.borrow_state.get(local)


def _borrowed_args(node: e.Call, conventions: Sequence[Convention]) -> list[e.ShirExpr]:
    """The values that a call reads without taking them and without a written `poke`."""
    return [arg.value for arg, convention in zip(node.args, conventions, strict=True)
            if convention.read_only
            and arg.marker is not Convention.INOUT]


def reject_self_aliasing_copy(checker: 'BorrowChecker', expr: MethodLike) -> None:
    """CE2430: a bulk write may not read the array it is writing.

    Growing the destination may REALLOCATE its buffer, which leaves the source pointer
    dangling in the middle of the copy. The check compares PLACES rather than values,
    because `b.items.extend(b.items)` aliases exactly as `a.extend(a)` does. A refill
    (`a.fill(a[0])`) is the same fault one slot down; see `_reject_refill_from_own_slot`.
    The callee decides, never the name alone: a user extension named `extend` or `fill`
    is not the built-in method and writes nothing.
    """
    effect = callee_effect(expr.method, expr.callee_builtin_family)
    if effect.refills:
        _reject_refill_from_own_slot(checker, expr)
        return
    if not effect.bulk_writes or not expr.args:
        return
    place = _place_of(expr.receiver)
    if place is None or place != _place_of(_viewed_array(expr.args[0])):
        return
    checker.err.emit(er.ERR.CE2430, expr.args[0].loc, name=spelled(checker, expr.args[0]),
                     target=place)


def _viewed_array(expr: Expr) -> Expr:
    """The array a slice views: `a[0..2]` reads the storage of `a` (docs/design/slices.md).

    Only slice steps are seen through; an element step names no storage a second
    expression can share.
    """
    walked = walk_place(expr, Step.INDEX)
    if walked.node is not None and all(
            isinstance(step, IndexAccess) and step.is_slice for step in walked.path):
        return walked.node
    return expr


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
    checker.err.emit(er.ERR.CE2430, expr.args[0].loc,
                     name=spelled(checker, expr.args[0]),
                     target=spelled(checker, expr.receiver))


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


def apply_mode(checker: 'BorrowChecker', call: CallLike, arg: Expr, index: int,
               modes: Sequence[ParamMode], kind: CalleeKind) -> None:
    """Apply ONE declared parameter mode to the argument that lands on it.

    THE rule, shared by a plain call and an extension/perk method, so the two cannot
    disagree about what an unmarked argument does (docs/design/borrow-model.md S5).
    """
    mode = checker.callee_modes.mode_at(modes, index, kind)
    check_nom_marker(checker, call, arg, index, mode, kind)
    if mode.consumes:
        if not take_lent_iterator(checker, call, arg):
            consume(checker, arg)
    elif not mode.by_pointer:
        register_implicit_borrow(checker, arg)


def settle_receiver(checker: 'BorrowChecker', expr: MethodLike) -> None:
    """A `nom self` receiver is a consuming use of what the method was called on.

    The mode is DECLARATION-only, so nothing at the call site says `nom` and the
    diagnostic has to name the method instead (ruling R27). The name is recorded on the
    state here, beside the span the move already records, and only when this call is
    that move: a receiver that an earlier move already took keeps that move's facts.
    """
    if not receiver_mode(expr.callee_self_mode).consumes:
        return
    receiver = expr.receiver
    # A built-in that reads a borrowed receiver through (`or_err`) leaves it to its owner.
    if (read_through_receiver(checker, expr) is not None
            and source_provenance(checker, receiver) is Provenance.BORROWED):
        receiver.ownership_provenance = Provenance.BORROWED
        reject_read_through_outside_try(checker, expr)
        return
    if take_lent_iterator(checker, expr, receiver):
        return
    state = checker.borrow_state.get(receiver.id) if isinstance(receiver, Name) else None
    already_moved = state is not None and state.is_moved
    consume(checker, receiver, CopyUse(f"call '{expr.method}' on the copy",
                                       f".{expr.method}({_arguments_text(checker, expr.args)})"))
    if state is not None and state.is_moved and not already_moved:
        state.consumed_by_method = expr.method


def _arguments_text(checker: 'BorrowChecker', args) -> str:
    """The arguments of a call as source text, or `...` when one has no short spelling."""
    texts = [spelled(checker, arg) for arg in args or ()]
    return "..." if "<expression>" in texts else ", ".join(texts)


def reject_read_through_outside_try(checker: 'BorrowChecker', expr: MethodLike) -> None:
    """CE2522: a read-through `or_err` stands under `??` and nowhere else.

    Its Result holds a borrowed Ok and an owned Err. Only `??` takes the two apart: the
    Err moves out, and the Ok binds a borrow (docs/design/error-conversion.md section 4).
    """
    if id(expr) in checker.try_operands:
        return
    from sushi_lang.semantics.generics.type_display import display_type
    receiver = spelled(checker, expr.receiver)
    checker.err.emit_with(er.ERR.CE2522, expr.loc,
                          maybe=display_type(expr.resolved_enum_type)) \
        .help(f"put the call under '??': `{receiver}.{expr.method}(...)??`, or take an "
              f"owned copy first: `{receiver}.clone().{expr.method}(...)`").emit()


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


def apply_destroy_effects(checker: 'BorrowChecker', node: e.Call, callee: Free) -> None:
    """Mark each argument the callee destroys through a `poke` parameter (#168).

    The summary of the `effects` pass is keyed by the written name of the function.
    """
    for index in checker.destroy_effects.get(checker.decl_name(callee.decl), ()):
        if index >= len(node.args):
            continue
        value = node.args[index].value
        if isinstance(value, e.LocalRef) and value.local in checker.borrow_state:
            checker.borrow_state[value.local].is_destroyed = True
