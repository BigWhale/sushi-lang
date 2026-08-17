"""Walkers over the shapes that read a value out of storage something else owns."""

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
from sushi_lang.semantics.ownership import is_get_out_container
from sushi_lang.semantics.typesys import ReferenceType, StructType, Type

if TYPE_CHECKING:
    from . import BorrowChecker


def member_access_base(expr: MemberAccess) -> Expr:
    """Get the base variable of a member access chain."""
    current = expr
    while isinstance(current, MemberAccess):
        current = current.receiver
    return current


def root_owner(expr: Optional[Expr]) -> Optional[str]:
    """The named local a read-through-an-owner expression ultimately reads out of."""
    while True:
        if isinstance(expr, TryExpr):
            expr = expr.expr
        elif isinstance(expr, MemberAccess):
            expr = expr.receiver
        elif isinstance(expr, IndexAccess):
            expr = expr.array
        elif isinstance(expr, (MethodCall, DotCall)):
            expr = expr.receiver
        elif isinstance(expr, Name):
            return expr.id
        else:
            return None


def reads_through_owner(checker: 'BorrowChecker', expr: Optional[Expr]) -> bool:
    """Does `expr` read a value out of storage something else still owns?"""
    while isinstance(expr, TryExpr):
        expr = expr.expr

    if is_bare_enum_constant(checker, expr):
        # `Shape.Empty` parses as a MemberAccess but CONSTRUCTS a payload-free
        # variant and owns it, like the `Shape.Empty()` spelling. Classifying it
        # BORROWED skipped cleanup and a later owning rebind leaked (#289).
        return False

    if isinstance(expr, (MemberAccess, IndexAccess)):
        return True

    if getattr(expr, "method", None) == "get":
        receiver = getattr(expr, "receiver", None)
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
    tables = checker.tables
    enums = getattr(tables, "enums", None) if tables is not None else None
    enum_type = getattr(enums, "by_name", {}).get(expr.receiver.id) if enums else None
    get_variant = getattr(enum_type, "get_variant", None)
    return get_variant is not None and get_variant(expr.member) is not None


def read_type(checker: 'BorrowChecker', expr: Optional[Expr]) -> Optional[Type]:
    """The type a read-through-an-owner expression produces."""
    while isinstance(expr, TryExpr):
        expr = expr.expr

    if isinstance(expr, Name):
        state = checker.borrow_state.get(expr.id)
        return state.var_type if state is not None else None

    if isinstance(expr, MemberAccess):
        receiver = checker.types.resolve_named(read_type(checker, expr.receiver))
        if isinstance(receiver, ReferenceType):
            receiver = checker.types.resolve_named(receiver.referenced_type)
        if isinstance(receiver, StructType):
            return receiver.get_field_type(expr.member)
        return None

    if isinstance(expr, IndexAccess):
        return checker.types.element_type(read_type(checker, expr.array))

    if getattr(expr, "method", None) == "get":
        receiver_type = read_type(checker, getattr(expr, "receiver", None))
        # A container `.get()` returns `Maybe@(T)`, and every use of it reaches here
        # through the `??` this method already unwrapped, so the interesting type is
        # the element. `Own@(T).get()` hands back the bare `T`.
        return checker.types.element_type(receiver_type)

    return None
