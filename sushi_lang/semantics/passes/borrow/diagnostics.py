"""Rendering for the relational borrow diagnostics -- each points at its second location."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import DotCall, Expr, MethodCall
from sushi_lang.semantics.generics.iterators import CONTAINER_ITERATORS
from sushi_lang.semantics.generics.opaque import bound_hint, note_opaque
from sushi_lang.semantics.generics.types import TypeParameter, substitute_type_params
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.source_text import source_text
from sushi_lang.semantics.typesys import ReferenceType

from .reads import borrow_owner, read_type
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
    moved_at = state.moved_at_span
    if moved_at is None:
        er.raise_internal_error("CE0150", name=name)
    method = state.consumed_by_method
    if method is not None:
        diag = checker.err.emit_with(er.ERR.CE2435, use_span,
                                     name=name, method=method)
        diag.note_at(f"'{name}' was consumed by '{method}' here", moved_at)
        diag.emit()
        return
    diag = checker.err.emit_with(er.ERR.CE2405, use_span, name=name)
    diag.note_at(f"'{name}' was moved here", moved_at)
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
    owner_local, what = state.invalidated_by
    owner = checker.name_of(owner_local)
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
    text = spelled(checker, iterable)
    diag = checker.err.emit_with(er.ERR.CE2412, span,
                                 owner=owner, name=text)
    if header is not None:
        diag.note_at(f"the loop walks '{text}' from here to the loop exit", header)
    if (not isinstance(iterable, MethodLikeNodes)
            or iterable.method not in CONTAINER_ITERATORS):
        # A lent iterator held by a `let` (#1259), or a lent value such as an adapter
        # chain (#1228): the change is to one of its owners.
        diag.help(f"{what} after the loop")
        diag.emit()
        return
    walked = cast(MethodCall, iterable).receiver
    receiver = spelled(checker, walked)
    receiver_type = read_type(checker, walked)
    if refuses_clone(checker, receiver_type):
        diag.help(f"{what} after the loop: "
                  f"{no_clone_reason(checker, receiver, receiver_type)}")
    else:
        diag.help(f"{what} after the loop, or walk an independent value: "
                  f"`{receiver}.clone().{iterable.method}()`")
    diag.emit()


MethodLikeNodes = (MethodCall, DotCall)


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

    The one type test of every `.share()` escape. It reads the rungs the typecheck pass
    reads for an instance call (`resolve_method`): a perk implementation first, then an
    extension method. A generic-target method has no row until a call cuts its copy, so
    the template that gives the instance the method answers too, with its return type
    substituted (#1196). A static, a method with arguments, or one that answers another
    type is no second owner.
    """
    from sushi_lang.semantics.generics.extensions import (
        target_copy_substitution, target_methods_of)
    tables = checker.tables
    types = checker.types
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    ty = types.resolve_named(ty)
    if ty is None:
        return False
    perk_method = tables.perk_impls.get_method(ty, "share")
    if perk_method is not None:
        return not perk_method.params and types.resolve_named(perk_method.ret) == ty
    method = tables.extensions.get_method(ty, "share")
    ret = method.ret_type if method is not None else None
    if method is None:
        found = target_methods_of(tables.generic_extensions, ty, "share", tables)
        if not found:
            return False
        method = found[0]
        ret = (substitute_type_params(method.ret_type,
                                      target_copy_substitution(method, ty))
               if method.ret_type is not None else None)
    return (not method.is_static and not method.params
            and types.resolve_named(ret) == ty)


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


def emit_consume_of_read(checker: 'BorrowChecker', expr: e.ShirExpr,
                         use_of_copy: Optional[CopyUse] = None) -> None:
    """Report CE2411 for a read through a live owner (`r??` over a borrowed `r`), with the
    read spelled as the user wrote it."""
    text = spelled(checker, expr)
    diag = checker.err.emit_with(er.ERR.CE2411, expr.loc, name=text)
    owner = borrow_owner(expr)
    state = checker.borrow_state.get(owner) if owner is not None else None
    if state is not None and state.declared_at_span is not None:
        diag.note_at(f"'{state.name}' owns this value and still frees it",
                     state.declared_at_span)
    # The OWNER's type answers the escape question: a read through it can only be
    # refused when what is read owns something, and it is the owner that holds it.
    owner_type = state.var_type if state is not None else None
    # ONE branch, on purpose: a get-out `.clone()` was a real defect (an internal error of
    # the deleted AST emitter), not a reason to word around it. The three RED
    # `test_own_get_*` files hold the branch honest until it is fixed.
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
    if state.is_lent_iterator:
        from .iterators import lent_iterator_help
        diag.help(lent_iterator_help(state.borrows_from)).emit()
        return
    diag.help(escape_help(checker, name, state.var_type, use_of_copy=use_of_copy,
                          through_receiver=state.is_method_receiver))
    diag.emit()


def _note_opaque_mover(checker: 'BorrowChecker', diag, ty) -> None:
    """A note at the type parameter whose value moves, when it does not promise `Clone`."""
    param = opaque_without_clone(checker, ty)
    if param is not None:
        note_opaque(diag, param)


#: The stand-in of an expression whose source text the pass cannot read.
NO_TEXT = "<expression>"


def spelled(checker: 'BorrowChecker', node) -> str:
    """The source text of `node`, as the user wrote it (`semantics/source_text.py`)."""
    return source_text(checker.source, node.loc) or NO_TEXT


def temporary_spelled(checker: 'BorrowChecker', place: e.ShirExpr) -> str:
    """The text of a borrow of a temporary (CE2404): each field of the place, after a root
    that a call of a function or a construction gives is the stand-in (`<expression>.x`).
    An integer literal spells its value; any other root spells its source text."""
    walked = walk_place(place, Step.MEMBER)
    root = walked.node
    if isinstance(root, e.Lit) and isinstance(root.value, e.IntConst):
        text = str(root.value.value)
    elif isinstance(root, e.Call) and isinstance(root.target, e.Dotted):
        text = spelled(checker, root)
    elif isinstance(root, (e.Lit, e.Call, e.StructInit, e.EnumInit, e.TupleInit, e.ArrayInit)):
        text = NO_TEXT
    else:
        text = spelled(checker, root)
    return ".".join([text, *(field.name for field in reversed(walked.path))])
