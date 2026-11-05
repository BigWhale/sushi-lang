"""The borrow half of the borrowed return `peek T` (#1229, docs/design/borrow-model.md S10h,
docs/design/ir.md 7.9).

Two sides of one rule. In the BODY of a `peek` function, each returned value must be a
borrow whose owner is a source of the function (CE2443), and the `return` gives the view to
the caller as it is: the value gets an `adopt` row, and nothing is moved. At a CALL, the
answer is a view of the owner of each argument at a position of the source row of the call
(ir.md 7.3): each owner is frozen while the answer lives, also when the answer is of a plain
type, and a temporary source argument is legal in one statement and refused in a `let`
(CE2444).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.borrowed_return import (
    RECEIVER_POSITION, BorrowedReturn, borrowed_return_of, sources_in)
from sushi_lang.semantics.ownership import Provenance, TypeClass
from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.body import Instance, ShirBody
from sushi_lang.semantics.shir.callee import Method

from . import arms
from .diagnostics import read_spelled, spelled
from .reads import borrow_owner, names_an_owner, read_type

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId

    from . import BorrowChecker
    from .state import Owner


@dataclass(frozen=True)
class BodyReturn:
    """The borrowed return of the body that the pass walks: its record, its source set by
    name and by local, and the locals of its parameters.

    `template_copy` is True for a monomorphized copy of a template. A value that owns
    nothing in the copy may be a value of a type parameter in the template, and the
    template check judged it there.
    """
    record: BorrowedReturn
    sources: tuple[str, ...]
    source_locals: frozenset[LocalId]
    params: frozenset[LocalId]
    template_copy: bool = False


def body_return(checker: 'BorrowChecker', decl: object,
                body: ShirBody) -> Optional[BodyReturn]:
    """The borrowed return of `body`, with its source set judged over the tables, or None."""
    record = borrowed_return_of(decl)
    if record is None:
        return None
    sources = sources_in(record, checker.tables)
    by_name = {checker.name_of(param.local): param.local for param in body.params}
    return BodyReturn(record, sources,
                      frozenset(by_name[name] for name in sources if name in by_name),
                      frozenset(by_name.values()),
                      isinstance(decl, GenericFuncDef) and isinstance(body.origin, Instance))


# ---------------------------------------------------------------------------------------
# The call side


def borrowed_call(checker: 'BorrowChecker', node: object) -> Optional[e.Call]:
    """The call of a callable with a `peek` return that `node` is, under each `??`, or
    None."""
    node = walk_place(node, Step.TRY).node
    if isinstance(node, e.Call) and checker.answers().sources_of(node.shir_id) is not None:
        return node
    return None


def call_sources(checker: 'BorrowChecker', call: e.Call) -> tuple[e.ShirExpr, ...]:
    """The arguments of a borrowed call at the positions of its source row: the receiver of
    a method, and each source argument."""
    answers = checker.answers()
    resolved = answers.callee_of(call.shir_id)
    out: list[e.ShirExpr] = []
    for position in answers.sources_of(call.shir_id) or ():
        if position == RECEIVER_POSITION:
            if resolved is not None and isinstance(resolved.callee, Method):
                out.append(resolved.callee.recv)
        elif position < len(call.args):
            out.append(call.args[position].value)
    return tuple(out)


def call_owners(checker: 'BorrowChecker',
                call: e.Call) -> tuple[tuple[Owner, ...], Optional[e.ShirExpr]]:
    """The owners that the answer of a borrowed call views, and its first temporary source.

    A source that is the answer of a borrowed call views the owners of that call. A literal
    is storage that the program keeps: it has no owner. A place views its root and the
    owners of the root (`view_owners`). Any other source is a temporary.
    """
    from .consume import view_owners
    owners: tuple[Owner, ...] = ()
    temporary: Optional[e.ShirExpr] = None
    for source in call_sources(checker, call):
        inner = borrowed_call(checker, source)
        found: tuple[Owner, ...] = ()
        if inner is not None:
            found, temp = call_owners(checker, inner)
            temporary = temporary or temp
        elif names_an_owner(checker, source):
            owner = borrow_owner(checker, source)
            found = view_owners(checker, owner) if owner is not None else ()
        elif not isinstance(source, e.Lit):
            temporary = temporary or source
        owners += tuple(owner for owner in found if owner not in owners)
    return owners, temporary


def reject_view_of_temporary(checker: 'BorrowChecker', value: e.ShirExpr) -> bool:
    """CE2444 for a `let` of the answer of a borrowed call over a temporary. True when
    refused."""
    call = borrowed_call(checker, value)
    if call is None:
        return False
    _owners, temporary = call_owners(checker, call)
    if temporary is None:
        return False
    callee = (call.target.name if isinstance(call.target, e.Dotted)
              else checker.decl_name(call.target.ref.decl).rpartition(".")[2]
              if isinstance(call.target, e.Named) and isinstance(call.target.ref, e.GlobalRef)
              else spelled(checker, call))
    checker.err.emit_with(er.ERR.CE2444, value.loc, callee=callee) \
        .note_at("this argument is a temporary", temporary.loc) \
        .help("bind the argument first and give the binding, or copy the answer with "
              "`.clone()`").emit()
    return True


# ---------------------------------------------------------------------------------------
# The body side


def check_borrowed_return(checker: 'BorrowChecker', value: e.ShirExpr) -> None:
    """A `return` in a `peek` body: the value, or the payload of `Result.Ok(...)`, must be a
    borrow whose owner is a source (CE2443). `Result.Err(...)` is an ordinary return."""
    from .consume import consume, decide, source_class

    variant = _result_variant(checker, value)
    if variant == "Err":
        arms.check(checker, value)
        consume(checker, value)
        return
    if variant == "Ok":
        decide(checker, value, Provenance.FRESH, source_class(checker, value))
        if not value.payload:
            return
        value = value.payload[0].value
    arms.check(checker, value)
    returned = checker.borrowed_return
    if returned is not None and returned.sources:
        reason = _why_not_a_view(checker, returned, value)
        if reason is not None and not (returned.template_copy and _owns_nothing(checker, value)):
            _emit_not_a_view(checker, returned, value, reason)
    # The view goes to the caller as it is: nothing here frees it, and nothing moves.
    decide(checker, value, Provenance.FRESH, source_class(checker, value))


def _result_variant(checker: 'BorrowChecker', value: e.ShirExpr) -> Optional[str]:
    """`Ok` or `Err` when `value` constructs a variant of a `Result`."""
    from sushi_lang.semantics.type_predicates import is_instance_of
    if not isinstance(value, e.EnumInit) or value.variant not in ("Ok", "Err"):
        return None
    return value.variant if is_instance_of(read_type(checker, value), "Result") else None


def _owns_nothing(checker: 'BorrowChecker', value: e.ShirExpr) -> bool:
    """Is `value` of a type that owns nothing? A copy of it can dangle from nothing."""
    ty = read_type(checker, value)
    return ty is not None and checker.types.type_class(ty) is TypeClass.PLAIN


def _why_not_a_view(checker: 'BorrowChecker', returned: BodyReturn,
                    value: e.ShirExpr) -> Optional[str]:
    """Why `value` is not a borrow of a source, or None when it is one."""
    call = borrowed_call(checker, value)
    if call is not None:
        owners, temporary = call_owners(checker, call)
        return _TEMPORARY if temporary is not None else _outside(checker, returned, owners)
    if names_an_owner(checker, value):
        owner = borrow_owner(checker, value)
        if owner is None or owner in returned.source_locals:
            return None
        state = checker.borrow_state.get(owner)
        if state is not None and state.is_let_borrow:
            return (_outside(checker, returned, state.borrows_from) if state.borrows_from
                    else _TEMPORARY)
        if state is not None and state.borrows_from:
            # A plain copy of an answer still holds the freeze of its sources, so a
            # template judges it alike at every type argument.
            return _outside(checker, returned, state.borrows_from)
        if isinstance(value, e.LocalRef) and owner not in returned.params:
            return _OWNED
        return _outside(checker, returned, (owner,))
    return _TEMPORARY if isinstance(value, (e.Field, e.Index, e.Slice)) else _OWNED


_TEMPORARY = "it borrows a temporary, which is freed at the end of the statement"
_OWNED = "it is an owned value, not a borrow of a source"


def _outside(checker: 'BorrowChecker', returned: BodyReturn,
             owners: tuple[Owner, ...]) -> Optional[str]:
    """The reason for the first owner that is not a source, or None when all are."""
    for owner in owners:
        if owner in returned.source_locals or _is_a_view(checker, owner):
            continue
        name = checker.name_of(owner)
        if owner in returned.params:
            return f"it borrows '{name}', which is not a source of the answer"
        return f"it borrows the local '{name}', which the function frees"
    return None if owners else _TEMPORARY


def _is_a_view(checker: 'BorrowChecker', owner: Owner) -> bool:
    """Is `owner` a `let` of a view whose owners are known? Its owners are judged instead."""
    state = checker.borrow_state.get(owner)
    return (state is not None and state.is_let_borrow and not state.is_lent_iterator
            and bool(state.borrows_from))


def _emit_not_a_view(checker: 'BorrowChecker', returned: BodyReturn, value: e.ShirExpr,
                     reason: str) -> None:
    sources = ", ".join(f"'{source}'" for source in returned.sources) or "nothing"
    checker.err.emit_with(er.ERR.CE2443, value.loc,
                          value=read_spelled(checker, value), reason=reason) \
        .note_at(f"the answer is declared a borrow of {sources} here", returned.record.span) \
        .help(f"return a view of {sources}, or drop `peek` and return an owned value") \
        .emit()
