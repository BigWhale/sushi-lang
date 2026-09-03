"""Expression shape probes, and the walkers over what reads out of a live owner.

The three walkers below answer three different questions about the SAME shapes -- is this
a read through an owner (bool), what type does it produce (Type), which local does it
bottom out in (str). They live together and share `unwrap_try` because a fourth, narrower
copy that forgot the `??` is exactly how `outer.get(0)??.push(5)` went unstamped.
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.semantics.ast import (
    DotCall,
    Expr,
    IndexAccess,
    MemberAccess,
    MethodCall,
    Name,
    TryExpr,
)
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ownership import is_get_out_container
from sushi_lang.semantics.typesys import ReferenceType, StructType, Type

if TYPE_CHECKING:
    from . import BorrowChecker


def unwrap_try(expr: Optional[Expr]) -> Optional[Expr]:
    """Strip every `??` from an expression, leaving what it actually evaluates."""
    while isinstance(expr, TryExpr):
        expr = expr.expr
    return expr


def called_on(expr: Optional[Expr], *methods: str) -> Optional[Expr]:
    """The receiver of `expr` if it calls one of `methods`, else None.

    `MethodCall` and `DotCall` are separate nodes with the same three fields, and a
    caller here holds a bare `Expr` that may be neither.
    """
    if not isinstance(expr, (MethodCall, DotCall)) or expr.method not in methods:
        return None
    return expr.receiver


def member_access_base(expr: MemberAccess) -> Expr:
    """Get the base variable of a member access chain."""
    current = expr
    while isinstance(current, MemberAccess):
        current = current.receiver
    return current


def root_owner(expr: Optional[Expr]) -> Optional[str]:
    """The named local a read-through-an-owner expression ultimately reads out of."""
    while True:
        match expr:
            case Name():
                return expr.id
            case TryExpr():
                expr = expr.expr
            case MemberAccess() | MethodCall() | DotCall():
                expr = expr.receiver
            case IndexAccess():
                expr = expr.array
            case _:
                return None


def chain_call_boundary(expr: Optional[Expr]) -> Optional[Span]:
    """The boundary that keeps a write receiver from reaching a root, or None (#352).

    A write receiver must reach its root -- a NAME -- through member and index steps
    only. Whatever else the walk stops at (a method call, a `??`, a plain call, an
    inline constructor) yields a temporary copy, so a write receiver whose chain
    crosses it cannot reach the storage it appears to write. Deliberately inverted --
    "a Name is fine" rather than a list of boundary kinds -- so a new expression kind
    is rejected, never silently writable. The boundary's span is the second location
    of the CE2429 diagnostic.
    """
    if expr is None:
        return None
    while True:
        match expr:
            case MemberAccess():
                expr = expr.receiver
            case IndexAccess():
                expr = expr.array
            case Name() | None:
                return None
            case _:
                return expr.loc


def reads_through_owner(checker: 'BorrowChecker', expr: Optional[Expr]) -> bool:
    """Does `expr` read a value out of storage something else still owns?"""
    expr = unwrap_try(expr)

    if is_bare_enum_constant(checker, expr):
        # `Shape.Empty` parses as a MemberAccess but CONSTRUCTS a payload-free
        # variant and owns it, like the `Shape.Empty()` spelling. Classifying it
        # BORROWED skipped cleanup and a later owning rebind leaked (#289).
        return False

    if isinstance(expr, (MemberAccess, IndexAccess)):
        return True

    receiver = called_on(expr, "get", "first", "last")
    if receiver is not None:
        if isinstance(receiver, Name):
            state = checker.borrow_state.get(receiver.id)
            return state is not None and is_get_out_container(state.var_type)
        return reads_through_owner(checker, receiver)

    return False


def is_bare_enum_constant(checker: 'BorrowChecker', expr: Optional[Expr]) -> bool:
    """Is `expr` the parenthesis-free spelling of a payload-free variant (#289)?"""
    if not isinstance(expr, MemberAccess) or not isinstance(expr.receiver, Name):
        return False
    if expr.receiver.id in checker.borrow_state:
        return False  # a local shadows the enum name
    # A GENERIC enum's bare variant carries the interned instance as a stamp: the
    # receiver is written `Maybe` and the table holds `Maybe<string>` (#545).
    enum_type = getattr(expr, "resolved_enum_type", None)
    if enum_type is None:
        tables = checker.tables
        enums = getattr(tables, "enums", None) if tables is not None else None
        enum_type = getattr(enums, "by_name", {}).get(expr.receiver.id) if enums else None
    get_variant = getattr(enum_type, "get_variant", None)
    return get_variant is not None and get_variant(expr.member) is not None


def read_type(checker: 'BorrowChecker', expr: Optional[Expr]) -> Optional[Type]:
    """The type a read-through-an-owner expression produces."""
    if isinstance(expr, TryExpr):
        # The typecheck pass stamps what a `??` unwraps to. Answering the WRAPPER's type
        # instead classified `r??` over a `Result@(i32, string)` as owning, and a plain
        # payload read out of a borrowed wrapper was refused (#548).
        unwrapped = expr.inferred_unwrapped_type
        if unwrapped is not None:
            return unwrapped
    expr = unwrap_try(expr)

    match expr:
        case Name():
            state = checker.borrow_state.get(expr.id)
            if state is not None:
                return state.var_type
            return constant_type(checker, expr.id)
        case MemberAccess():
            return _field_type(checker, expr)
        case IndexAccess():
            return checker.types.element_type(read_type(checker, expr.array))

    receiver = called_on(expr, "get", "first", "last")
    if receiver is None:
        return None
    # A container `.get()` returns `Maybe@(T)`, and every use of it reaches here through
    # the `??` already unwrapped, so the interesting type is the element. `Own@(T).get()`
    # hands back the bare `T`.
    return checker.types.element_type(read_type(checker, receiver))


def constant_type(checker: 'BorrowChecker', name: str) -> Optional[Type]:
    """The declared type of a CONSTANT or a unit variable, for a bare name that is no local.

    A constant lives in `.rodata` and can never be moved out of, so an index into one
    is a read through an owner like any other. Without its type the read answered "no
    type", the class came back PLAIN, and the consuming use passed a check the backend
    then answered with CE0129 (#498).
    """
    tables = getattr(checker, "tables", None)
    constants = getattr(tables, "constants", None) if tables is not None else None
    if constants is None:
        return None
    sig = constants.lookup(name, checker.unit_name, checker.scope)
    return getattr(sig, "const_type", None)


def unit_variables(checker: 'BorrowChecker'):
    """Every `var` this function can name, `(name, ConstSig)` -- the unit's own first.

    A unit variable is storage no function owns, so it gets a `BorrowState` at function
    entry (`is_unit_var`) rather than at a `let`: borrowable, freezable by a `let`-borrow
    out of it, and never moved out of (docs/design/unit-storage.md).
    """
    tables = getattr(checker, "tables", None)
    constants = getattr(tables, "constants", None) if tables is not None else None
    if constants is None:
        return []
    visible = {**constants.by_name, **constants.by_unit.get(checker.unit_name, {})}
    return [(name, sig) for name, sig in visible.items() if sig.is_var]


def _field_type(checker: 'BorrowChecker', expr: MemberAccess) -> Optional[Type]:
    """The declared type of the field a `MemberAccess` names."""
    receiver = checker.types.resolve_named(read_type(checker, expr.receiver))
    if isinstance(receiver, ReferenceType):
        receiver = checker.types.resolve_named(receiver.referenced_type)
    if isinstance(receiver, StructType):
        return receiver.get_field_type(expr.member)
    return None
