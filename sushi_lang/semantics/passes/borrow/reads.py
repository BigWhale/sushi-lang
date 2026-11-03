"""Expression shape probes, and the readers of what reads out of a live owner.

`read_type` and `borrow_owner` read a SHIR node: the type of the value of a node, and the
local that a borrow of a place is counted on and that a binding into the place freezes.
The other probes read the AST. They live together and share `unwrap_try` because a
narrower copy that forgot the `??` is exactly how `outer.get(0)??.push(5)` went unstamped.

Port templates: the probes that read the AST take an AST node, and no SHIR pass has one.
No pass reaches them today. The group that checks each construct ports its probes to SHIR
inputs before a SHIR caller is connected: B7 a `nom` binder of a match (`root_owner`),
A6 an element and a slice, A6 or B2 a container get-out (`called_on`, `reads_through_owner` with
`is_bare_enum_constant`, `receiver_type`, `names_kept_storage`), A8 a constant
(`constant_sig`, `constant_type`), and D1 a `var` (`unit_variables`).
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.semantics.ast import (
    ArrayLiteral,
    Call,
    DotCall,
    DynamicArrayFrom,
    DynamicArrayNew,
    Expr,
    IndexAccess,
    MemberAccess,
    MethodCall,
    Name,
)
from sushi_lang.internals.report import Span
from sushi_lang.semantics.places import Step, unwrap_try, walk_place
from sushi_lang.semantics.ownership import is_get_out_container
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.ids import LocalId

    from . import BorrowChecker

# A read through an owner crosses every step: a field, an element, a call's receiver, a `??`.
OWNER_STEPS = Step.MEMBER | Step.INDEX | Step.CALL | Step.TRY


def called_on(expr: Optional[Expr], *methods: str) -> Optional[Expr]:
    """The receiver of `expr` if it calls one of `methods`, else None.

    `MethodCall` and `DotCall` are separate nodes with the same three fields, and a
    caller here holds a bare `Expr` that may be neither.
    """
    if not isinstance(expr, (MethodCall, DotCall)) or expr.method not in methods:
        return None
    return expr.receiver


def root_owner(expr: Optional[Expr]) -> Optional[str]:
    """The named local a read-through-an-owner expression ultimately reads out of."""
    root = walk_place(expr, OWNER_STEPS).name
    return root.id if root is not None else None


def borrow_owner(node: object) -> Optional[LocalId]:
    """The local that a borrow of the SHIR place `node` is counted on, and that a binding
    into `node` freezes: the root local, under each field step and each `??` that reads
    it. The pass tracks whole locals, not the places in them.

    An element step comes with the group that checks an element.
    """
    return walk_place(node, Step.MEMBER | Step.TRY).local


def chain_call_boundary(expr: object) -> Optional[Span]:
    """The boundary that keeps a write receiver from reaching a root, or None (#352).

    A write receiver must reach its root -- a NAME, or the local of a SHIR place --
    through member and index steps only. Whatever else the walk stops at (a method call, a `??`, a plain call, an
    inline constructor) yields a temporary copy, so a write receiver whose chain
    crosses it cannot reach the storage it appears to write. Deliberately inverted --
    "a Name is fine" rather than a list of boundary kinds -- so a new expression kind
    is rejected, never silently writable. The boundary's span is the second location
    of the CE2429 diagnostic.
    """
    end = walk_place(expr, Step.MEMBER | Step.INDEX).node
    if end is None or isinstance(end, (Name, e.LocalRef)):
        return None
    return end.loc


def slice_on_path(expr: object) -> Optional[IndexAccess | e.Slice]:
    """The slice a write through `expr` crosses, or None (S4, docs/design/slices.md).

    A slice is a read-only view: a write through it is lost from no one, but a growth
    through it would move storage that the array keeps.
    """
    for node in walk_place(expr, Step.MEMBER | Step.INDEX).path:
        if (isinstance(node, IndexAccess) and node.is_slice) or isinstance(node, e.Slice):
            return node
    return None


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
        # A temporary container is an owner too: the backend parks it and frees its
        # elements at scope exit, so a get-out from it is a borrow (#1170).
        return (reads_through_owner(checker, receiver)
                or is_get_out_container(receiver_type(checker, receiver)))

    return False


def receiver_type(checker: 'BorrowChecker', receiver: Optional[Expr]) -> Optional[Type]:
    """The type of a get-out's receiver: a place, or a temporary that makes a new value.

    The typecheck pass stamps the type of each temporary: a call carries what it answers,
    and an array literal, a `from([...])` and a `new()` carry the array they make.
    """
    ty = read_type(checker, receiver)
    if ty is not None:
        return ty
    match receiver:
        case Call() | MethodCall() | DotCall():
            return checker.types.resolve_named(receiver.inferred_return_type)
        case ArrayLiteral() | DynamicArrayFrom() | DynamicArrayNew():
            return receiver.resolved_type
    return None


def names_kept_storage(checker: 'BorrowChecker', expr: Optional[Expr]) -> bool:
    """Is `expr` storage that a named local keeps, and not an owned temporary (#1014)?

    A place (member and index steps to a name) is kept storage, and so is a get-out
    (`h.get(0)??`) whose receiver is kept storage. Any other call makes a new owned value
    (`a.clone()`, `a.s(0, 3)`), and nothing keeps that value but its reader.
    """
    end = walk_place(expr, Step.MEMBER | Step.INDEX).node
    if isinstance(end, Name):
        return True
    receiver = called_on(unwrap_try(end), "get", "first", "last")
    if receiver is None or not reads_through_owner(checker, end):
        return False
    return names_kept_storage(checker, receiver)


def is_bare_enum_constant(checker: 'BorrowChecker', expr: Optional[Expr]) -> bool:
    """Is `expr` the parenthesis-free spelling of a payload-free variant (#289)?"""
    if not isinstance(expr, MemberAccess) or not isinstance(expr.receiver, Name):
        return False
    if expr.receiver.id in checker.borrow_state:
        return False  # a local shadows the enum name
    # A GENERIC enum's bare variant carries the interned instance as a stamp: the
    # receiver is written `Maybe` and the table holds `Maybe<string>` (#545).
    enum_type = expr.resolved_enum_type
    if enum_type is None:
        enum_type = checker.tables.enums.by_name.get(expr.receiver.id)
    get_variant = getattr(enum_type, "get_variant", None)
    return get_variant is not None and get_variant(expr.member) is not None


def read_type(checker: 'BorrowChecker', node: object) -> Optional[Type]:
    """The type of the value that the SHIR node `node` makes or reads.

    A local has the type of its state, which keeps the reference type of a `peek` or a
    `poke` parameter. Every other node has the type that the typecheck pass answered: a
    `??` answers what it unwraps.
    """
    if isinstance(node, e.LocalRef):
        state = checker.borrow_state.get(node.local)
        return state.var_type if state is not None else checker.local_type(node.local)
    if isinstance(node, e.ExprNode):
        return checker.answers().type_of(node.shir_id)
    return None


def constant_sig(checker: 'BorrowChecker', name: str):
    """The record a bare name reaches in the constant table, a `const` or a `var`.

    The lookup is SCOPED and never `by_name`: the flat view holds one record per name
    over the whole program and is first-wins, so it answers with another unit's
    declaration of the same name (#685, `docs/design/unit-namespaces.md` section 9).
    A name that two imports offer reaches no record: CE3012 refused it, and the first
    candidate is not the one the user chose.
    """
    sig = checker.tables.constants.lookup(name, checker.unit_name, checker.scope)
    if sig is None or checker.tables.visibility.is_ambiguous(
            "variable" if sig.is_var else "constant", name, checker.unit_name,
            checker.scope):
        return None
    return sig


def constant_type(checker: 'BorrowChecker', name: str) -> Optional[Type]:
    """The declared type of a CONSTANT or a unit variable, for a bare name that is no local.

    A constant lives in `.rodata` and can never be moved out of, so an index into one
    is a read through an owner like any other. Without its type the read answered "no
    type", the class came back PLAIN, and the consuming use passed a check the backend
    then answered with CE0129 (#498).
    """
    return getattr(constant_sig(checker, name), "const_type", None)


def unit_variables(checker: 'BorrowChecker'):
    """Every `var` this function can name, `(name, ConstSig)` -- the unit's own first.

    A unit variable is storage no function owns, so it gets a `BorrowState` at function
    entry (`is_unit_var`) rather than at a `let`: borrowable, freezable by a `let`-borrow
    out of it, and never moved out of (docs/design/unit-storage.md).
    """
    constants = checker.tables.constants
    own = (constants.by_unit.get(checker.unit_name, {})
           if checker.unit_name is not None else {})
    visible = {**constants.by_name, **own}
    return [(name, sig) for name, sig in visible.items() if sig.is_var]

