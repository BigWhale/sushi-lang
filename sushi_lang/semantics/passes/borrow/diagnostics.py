"""Rendering for the relational borrow diagnostics -- each points at its second location."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import (
    BinaryOp,
    DotCall,
    Expr,
    IndexAccess,
    IntLit,
    MemberAccess,
    MethodCall,
    Name,
    TryExpr,
)
from sushi_lang.semantics.generics.opaque import bound_hint, note_opaque
from sushi_lang.semantics.generics.types import TypeParameter
from sushi_lang.semantics.typesys import ReferenceType

from .reads import read_type, root_owner
from .state import BorrowState

if TYPE_CHECKING:
    from . import BorrowChecker


@dataclass(frozen=True)
class BorrowKind:
    """One reason a value may not be consumed, as DATA."""
    matches: Callable[[BorrowState], bool]
    note_span: Callable[[BorrowState], Optional[Span]]
    note: str


# Which kind of borrow is this? The same question `writes.READONLY_RECEIVERS` answers for
# a WRITE, answered here for a CONSUME. Most specific first, and unlike that table the
# order IS precedence: a row whose span is missing falls through to the next one, which
# is how a reference parameter with no span still gets the `is_borrow_param` sentence.
BORROW_KINDS: tuple[BorrowKind, ...] = (
    BorrowKind(
        matches=lambda state: state.bound_at_span is not None,
        note_span=lambda state: state.bound_at_span,
        note="'{name}' borrows here, and the owner keeps the value",
    ),
    BorrowKind(
        matches=lambda state: (isinstance(state.var_type, ReferenceType)
                               and state.declared_at_span is not None),
        note_span=lambda state: state.declared_at_span,
        note="'{name}' is declared here as a `{mode}` borrow of the caller's value",
    ),
    BorrowKind(
        # No span requirement, on purpose: this sentence still says something without a
        # location, so a receiver whose type span is missing degrades to a note rather
        # than to nothing. The row below needs its location to read at all.
        matches=lambda state: state.is_method_receiver,
        note_span=lambda state: state.declared_at_span,
        note="'self' is the receiver of a method on this type, which borrows the "
             "caller's value",
    ),
    BorrowKind(
        matches=lambda state: (state.is_borrow_param
                               and state.declared_at_span is not None),
        note_span=lambda state: state.declared_at_span,
        note="'{name}' is declared here, and a method parameter borrows the caller's "
             "value",
    ),
)


def emit_use_after_move(checker: 'BorrowChecker', name: str, use_span: Optional[Span],
                        state: BorrowState) -> None:
    """Report a use after the value left, pointing at where it left as well as the use.

    Two codes, and the method name is what tells them apart (ruling R27). A `nom`
    ARGUMENT is a real move with a visible marker, so it keeps CE2405. A consuming
    RECEIVER has no marker anywhere on the page -- a receiver's mode is
    declaration-only -- so CE2435 has to carry what the syntax cannot, and it names the
    method.

    A move that a foreach iterator reports (CE2412) is not reported again at its use on
    the next round (#995).
    """
    if state.move_reported_by is not None:
        return
    method = state.consumed_by_method
    if method is not None:
        diag = checker.err.emit_with(er.ERR.CE2435, use_span,
                                     name=name, method=method)
        if state.moved_at_span is not None:
            diag.note_at(f"'{name}' was consumed by '{method}' here", state.moved_at_span)
        diag.emit()
        return
    diag = checker.err.emit_with(er.ERR.CE2405, use_span, name=name)
    if state.moved_at_span is not None:
        diag.note_at(f"'{name}' was moved here", state.moved_at_span)
    param = opaque_without_clone(checker, state.var_type)
    if param is not None:
        note_opaque(diag, param).help(
            f"a value of the type parameter '{param.written()}' moves, because a type "
            f"argument can own; add 'Clone' to the constraints of '{param.written()}' "
            f"({bound_hint(param, 'Clone')}) and hand over `{name}.clone()` before "
            f"the move")
    diag.emit()


def emit_use_of_invalidated_borrow(checker: 'BorrowChecker', name: str,
                                   use_span: Optional[Span],
                                   state: BorrowState, *, by_the_change: bool = False) -> None:
    """Report CE2412 at the change, and name the use that makes it wrong.

    `by_the_change` is the use that IS an argument of the change: the call reads it while
    it changes the owner (#888), so the use is not later and the escape is a clone.
    """
    owner, what = state.invalidated_by
    diag = checker.err.emit_with(er.ERR.CE2412, state.invalidated_at,
                                 owner=owner, name=name)
    if state.bound_at_span is not None:
        diag.note_at(f"'{name}' borrows from '{owner}' here", state.bound_at_span)
    if use_span is not None:
        diag.note_at(f"'{name}' is read here, by the call that changes '{owner}'"
                     if by_the_change else f"'{name}' is used here, after the change",
                     use_span)
    no_clone = refuses_clone(checker, state.var_type)
    if by_the_change and no_clone:
        diag.help(f"{no_clone_reason(checker, name, state.var_type)}, so no call can "
                  f"read it while it "
                  f"changes '{owner}'")
    elif by_the_change:
        diag.help(f"pass an independent value: `{name}.clone()`")
    elif no_clone:
        diag.help(f"{what} after the last use of '{name}': "
                  f"{no_clone_reason(checker, name, state.var_type)}")
    else:
        diag.help(f"{what} after the last use of '{name}', "
                  f"or bind an independent value with `.clone()`")
    diag.emit()
    # Report once per binding. A suppressed (loop-discovery) pass reported nothing, so it
    # must consume nothing -- clearing there erases the real pass's invalidation.
    if not checker.err.suppressed:
        state.invalidated_at = None


def emit_change_under_iterator(checker: 'BorrowChecker', change: tuple,
                               iterable: Expr, header: Optional[Span]) -> None:
    """Report CE2412 at a change of the container a `foreach` still walks (#956).

    `change` is the span of the change and its cause, as `FlowFacts.invalidation_of`
    answers them.
    """
    span, (owner, what) = change
    text = expr_to_string(iterable)
    diag = checker.err.emit_with(er.ERR.CE2412, span,
                                 owner=owner, name=text)
    if header is not None:
        diag.note_at(f"the loop walks '{text}' from here to the loop exit", header)
    walked = cast(MethodCall, iterable).receiver
    receiver = expr_to_string(walked)
    receiver_type = read_type(checker, walked)
    if refuses_clone(checker, receiver_type):
        diag.help(f"{what} after the loop: "
                  f"{no_clone_reason(checker, receiver, receiver_type)}")
    else:
        diag.help(f"{what} after the loop, or walk an independent value: "
                  f"`{receiver}.clone().{iterable.method}()`")
    diag.emit()


def refuses_clone(checker: 'BorrowChecker', ty) -> bool:
    """Is `.clone()` refused on `ty` (CE2431, CE4018)? The one type test of every clone
    escape.

    A type that declares a resource, or holds one, has no clone (ruling R3), and an
    opaque type parameter that does not promise `Clone` has none either (#1070, R5). So
    a help must not offer one there.
    """
    from sushi_lang.semantics.typesys import holds_declared_resource
    return holds_declared_resource(ty, checker.types.drops,
                                   resolve=checker.types.resolve_named)


def opaque_without_clone(checker: 'BorrowChecker', ty) -> Optional[TypeParameter]:
    """The first opaque type parameter in `ty` that does not promise `Clone`, or None.

    The clone predicate names it (`holds_declared_resource`), so the help of a refused
    move in a template names the parameter that the fix goes on (#1070, R5).
    """
    from sushi_lang.semantics.typesys import holds_declared_resource
    found: list = []
    holds_declared_resource(ty, checker.types.drops, resolve=checker.types.resolve_named,
                            found=found)
    return next((t for t in found if isinstance(t, TypeParameter)), None)


def answers_share(checker: 'BorrowChecker', ty) -> bool:
    """Does `x.share()` compile on a value of `ty` and give a second `ty`? (#1023)

    The one type test of every `.share()` escape. It reads the two table rungs the
    typecheck pass reads for an instance call (`resolve_method`): a perk implementation
    first, then an extension method. A static, a method with arguments, or one that
    answers another type is no second owner.
    """
    types = checker.types
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    ty = types.resolve_named(ty)
    if ty is None:
        return False
    perk_method = checker.tables.perk_impls.get_method(ty, "share")
    if perk_method is not None:
        return not perk_method.params and types.resolve_named(perk_method.ret) == ty
    method = checker.tables.extensions.get_method(ty, "share")
    return (method is not None and not method.is_static and not method.params
            and types.resolve_named(method.ret_type) == ty)


@dataclass(frozen=True)
class CopyUse:
    """What a consuming position does with the copy that its clone escape takes.

    The help reads "clone it, and <clause>: `<text>.clone()<tail>`". An `as` conversion
    converts the copy, and a `nom self` call calls its method on the copy.
    """
    clause: str
    tail: str


def no_clone_reason(checker: 'BorrowChecker', text: str, ty) -> str:
    """The clause a help gives in place of a clone escape that is refused.

    A type that declares a resource has no clone (CE2431). An opaque type parameter
    has one when a constraint promises `Clone` (CE4018, #1070), so the clause names that
    constraint.
    """
    param = opaque_without_clone(checker, ty)
    if param is not None:
        return (f"'{text}' holds the type parameter '{param.written()}', which has a "
                f"clone only with 'Clone' in its constraints ({bound_hint(param, 'Clone')})")
    return f"'{text}' owns a resource and cannot be cloned"


def escape_help(checker: 'BorrowChecker', text: str, ty, value_type=None, *,
                handover: bool = True, use_of_copy: Optional[CopyUse] = None,
                through_receiver: bool = False) -> str:
    """What CE2411 offers as the way out, which depends on WHAT is being consumed.

    `.clone()` for an ordinary owning value. A resource type has no clone (CE2431), so
    offering one there would be a rejection with no escape -- the exact hole the clone
    totality gate exists to prevent (HANDLES.md ruling R3). For a resource the second
    owner is `.share()` when the type of `text` has one (#1023); otherwise the help names
    only the escapes that compile. `value_type` is the type of `text` when `ty` is the
    type of its owner. `handover` is False where a `nom` parameter is no escape (a write
    through a pattern binding). `use_of_copy` is what the position does with the copy:
    an `as` conversion (docs/design/error-conversion.md section 3.2) or a `nom self` call.
    `through_receiver` is a place of `self`: the method takes the receiver with
    `nom self` to own what it reads.
    """
    param = opaque_without_clone(checker, ty if value_type is None else value_type)
    if param is not None:
        name = param.written()
        clone = f"add 'Clone' to the constraints of '{name}' ({bound_hint(param, 'Clone')})"
        tail = use_of_copy.tail if use_of_copy is not None else ""
        if not handover:
            return f"{clone}, and take an independent value with `{text}.clone()`"
        take = ("take the receiver with `nom self`" if through_receiver
                else "take it with a `nom` parameter and pass it with `nom`")
        return (f"a value of the type parameter '{name}' moves, because a type argument "
                f"can own: {take}, or {clone} and take `{text}.clone(){tail}`")
    if not refuses_clone(checker, ty):
        if use_of_copy is not None:
            return (f"clone it, and {use_of_copy.clause}: "
                    f"`{text}.clone(){use_of_copy.tail}`")
        return f"clone it to take an independent value: `{text}.clone()`"
    no_clone = f"a descriptor cannot be deep-copied, so there is no `{text}.clone()`"
    if answers_share(checker, ty if value_type is None else value_type):
        return (f"{no_clone}; take a second owner with `{text}.share()`, or restructure "
                f"so only one owner is needed")
    no_share = f"{no_clone}, and its type has no `share()` that gives a second owner"
    if handover:
        return (f"{no_share}; hand the value over with a `nom` parameter, or restructure "
                f"so only one owner is needed")
    return f"{no_share}; restructure so only one owner is needed"


def write_escape(checker: 'BorrowChecker', text: str, ty) -> str:
    """The escape clause a refused write through a view offers after its own modes.

    The copy escape is the one with the "mutate it, and store it back" tail. A type
    that owns a resource has no copy (CE2431), so it gets only the escapes that compile
    (#1033).
    """
    if not refuses_clone(checker, ty):
        return (f"take an independent value with `{text}.clone()`, mutate it, and store "
                f"it back")
    return escape_help(checker, text, ty, handover=False)


def parameter_escape(checker: 'BorrowChecker', text: str, ty) -> str:
    """The tail a refused write through a parameter offers after its `poke` escape.

    The copy escape for a type that can be cloned, and only the escapes that compile for
    a type that owns a resource (CE2431, #1039).
    """
    if not refuses_clone(checker, ty):
        return f", or take an independent value with `{text}.clone()`"
    return " -- otherwise " + escape_help(checker, text, ty, handover=False)


def emit_consume_of_read(checker: 'BorrowChecker', expr: Expr,
                         use_of_copy: Optional[CopyUse] = None) -> None:
    """Report CE2411 for a read through a live owner (`h.inner`, `c.get(0)??`)."""
    text = expr_to_string(expr)
    diag = checker.err.emit_with(er.ERR.CE2411, expr.loc, name=text)
    owner = root_owner(expr)
    state = checker.borrow_state.get(owner) if owner is not None else None
    if state is not None and state.declared_at_span is not None:
        diag.note_at(f"'{owner}' owns this value and still frees it",
                     state.declared_at_span)
    # The OWNER's type answers the escape question: a read through it can only be
    # refused when what is read owns something, and it is the owner that holds it.
    owner_type = state.var_type if state is not None else None
    # ONE branch, on purpose: a get-out `.clone()` still hits CE0019, and that is a real
    # defect rather than a reason to word around it. The three RED `test_own_get_*` files
    # hold the branch honest until it is fixed.
    value_type = read_type(checker, expr)
    _note_opaque_mover(checker, diag, value_type)
    diag.help(escape_help(checker, text, owner_type, value_type,
                          use_of_copy=use_of_copy,
                          through_receiver=state is not None and state.is_method_receiver))
    diag.emit()


def emit_consume_of_borrow(checker: 'BorrowChecker', name: str,
                           use_span: Optional[Span], state: BorrowState,
                           use_of_copy: Optional[CopyUse] = None) -> None:
    """Report CE2411, pointing at the binding or declaration as well as the use."""
    diag = checker.err.emit_with(er.ERR.CE2411, use_span, name=name)
    for kind in BORROW_KINDS:
        if kind.matches(state):
            mode = getattr(state.var_type, "mutability", "")
            note_span = kind.note_span(state)
            if note_span is not None:
                diag.note_at(kind.note.format(name=name, mode=mode), note_span)
            break
    _note_opaque_mover(checker, diag, state.var_type)
    diag.help(escape_help(checker, name, state.var_type, use_of_copy=use_of_copy,
                          through_receiver=state.is_method_receiver))
    diag.emit()


def _note_opaque_mover(checker: 'BorrowChecker', diag, ty) -> None:
    """A note at the type parameter whose value moves, when it does not promise `Clone`."""
    param = opaque_without_clone(checker, ty)
    if param is not None:
        note_opaque(diag, param)


def expr_to_string(expr: Expr) -> str:
    """Render an expression back to source text, for a message the user can paste."""
    match expr:
        case Name():
            return expr.id
        case IntLit():
            return str(expr.value)
        case BinaryOp():
            return (f"({expr_to_string(expr.left)} {expr.op} "
                    f"{expr_to_string(expr.right)})")
        case MethodCall() | DotCall():
            # Both spellings reach here, arguments included, so the text matches what the
            # user wrote.
            args = ", ".join(_argument_text(a) for a in (expr.args or []))
            return f"{expr_to_string(expr.receiver)}.{expr.method}({args})"
        case MemberAccess():
            return f"{expr_to_string(expr.receiver)}.{expr.member}"
        case IndexAccess():
            return f"{expr_to_string(expr.array)}[{expr_to_string(expr.index)}]"
        case TryExpr():
            return f"{expr_to_string(expr.expr)}??"
        case _:
            return "<expression>"


def _argument_text(arg: Expr) -> str:
    """An argument as written: the `nom` marker is part of the call (`f(nom x)`)."""
    text = expr_to_string(arg)
    return f"nom {text}" if arg.nom_marked else text
