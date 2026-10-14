"""The writer of `Bindings`, the one new output of the scope pass (docs/design/ir.md 7.1, S3).

The scope pass makes one writer for each body: a function, an extension method, a perk
method or a constant initializer. A lambda is part of the body that holds it, so its
parameters take their ids from the same counter. The ids are given in the order that the
pass declares the binders: the receiver, the parameters, then the body in source order.
A `let` takes its id before its initializer, and the targets of a destructure after it.

When the body ends, the backstop walks it again: each name occurrence must have an
answer, each binder that `binders_of` finds must have a local, and each lambda must have
its captures. A gap is the internal error CE0153. The gate
`tests/unit/test_scope_binder_walk_is_total.py` holds `BINDER_FORMS` to the AST classes.
"""
from __future__ import annotations

from typing import Any, Callable, Iterator

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    EnumConstructor, Expand, ExtendDef, Foreach, FuncDef, Lambda, Let, MatchArm, Name, Node,
    destructure_binders, pattern_bindings,
)
from sushi_lang.semantics.ast_walk import walk_nodes
from sushi_lang.semantics.name_ladder import BareName
from sushi_lang.semantics.shir.bindings import (
    Bindings, Capture, Const, Function, Namespace, Resolution, TypeName, Unresolved,
)
from sushi_lang.semantics.shir.ids import IdAllocator, LocalId

Binders = Iterator[tuple[object, str]]

# The answer of each rung that names a declaration, or nothing. The scope pass answers
# LOCAL (the local of the binder) and CONSTANT (a `const`, or the `Storage` of a `var`).
DECLARED_ANSWERS: dict[BareName, Callable[[str], Resolution]] = {
    BareName.STDLIB_CONSTANT: Const,
    BareName.FUNCTION: Function,
    BareName.NAMESPACE: Namespace,
    BareName.TYPE: TypeName,
    BareName.NOTHING: Unresolved,
}


def _let_binders(stmt: Let) -> Binders:
    yield stmt, stmt.name
    for target in destructure_binders(stmt.targets):
        yield target, target.name


def _foreach_binders(stmt: Foreach) -> Binders:
    yield stmt, stmt.item_name


def _expand_binders(stmt: Expand) -> Binders:
    yield stmt, stmt.var


def _parameter_binders(node: Any) -> Binders:
    for param in node.params:
        yield param, param.name


def _arm_binders(arm: MatchArm) -> Binders:
    """A later `|` alternative binds the local of the first one, with its own key."""
    for name, owner, _span in pattern_bindings(arm.pattern, every_alternative=True):
        yield owner, name


# Each node class that declares a local, with its binders as (owner, name). The owner is
# the node that holds the binder: the node itself, a `Param`, a `DestructureTarget`, or
# the pattern that holds a pattern binding. The implicit `self` of a method has no node,
# so its owner is the method (`BindingsWriter.receiver`).
BINDER_FORMS: dict[type, Callable[[Any], Binders]] = {
    Let: _let_binders,
    Foreach: _foreach_binders,
    Expand: _expand_binders,
    Lambda: _parameter_binders,
    FuncDef: _parameter_binders,
    ExtendDef: _parameter_binders,
    MatchArm: _arm_binders,
}


def binders_of(node: object) -> Binders:
    """The binders that `node` declares, in source order: nothing for most nodes."""
    form = BINDER_FORMS.get(type(node))
    if form is not None:
        yield from form(node)


class BindingsWriter:
    """The `Bindings` of one body while the scope pass walks it.

    `receiver` is True for a body with an implicit `self`: an extension method and a perk
    method.
    """

    def __init__(self, body: object, receiver: bool = False) -> None:
        self.bindings = Bindings(body)
        self.receiver = receiver
        self._ids: IdAllocator[LocalId] = IdAllocator(LocalId)

    def binder(self, owner: object, name: str) -> LocalId:
        """The local of a binder. A binder that the walk meets again keeps its local."""
        known = self.bindings.local_of(owner, name)
        if known is not None:
            return known
        local = self._ids.allocate()
        self.bindings.record_binder(owner, name, local)
        return local

    def same_local(self, owner: object, name: str, local: LocalId) -> None:
        """A binder of a later `|` alternative: it binds the local of the first one."""
        self.bindings.record_binder(owner, name, local)

    def resolve(self, node: object, answer: Resolution) -> None:
        self.bindings.record_resolution(node, answer)

    def captures(self, lam: Lambda, captures: tuple[Capture, ...]) -> None:
        self.bindings.record_captures(lam, captures)

    def finish(self) -> Bindings:
        """The table of the body, once the backstop finds no gap in it."""
        _check_complete(self.bindings, self.receiver)
        return self.bindings


def _gap(what: str, name: str, node: object) -> None:
    er.raise_internal_error("CE0153", span=getattr(node, "loc", None), what=what, name=name)


def _check_complete(bindings: Bindings, receiver: bool) -> None:
    """CE0153 at the first name, binder or lambda of the body that the walk did not record.

    The `next()` call of a protocol walk is a node that the typecheck pass makes, and the
    scope pass never walks it, so the check does not enter it.
    """
    body = bindings.body
    if receiver and bindings.local_of(body, "self") is None:
        _gap("binder", "self", body)
    made_later: set[int] = set()

    def visit(node: Node) -> bool:
        if id(node) in made_later:
            return False
        if isinstance(node, Foreach) and node.protocol_next is not None:
            made_later.add(id(node.protocol_next))
        if isinstance(node, Name) and bindings.resolution_of(node) is None:
            _gap("name", node.id, node)
        if isinstance(node, EnumConstructor) and bindings.resolution_of(node) is None:
            _gap("name", node.enum_name, node)
        if isinstance(node, Lambda) and bindings.captures_of(node) is None:
            _gap("lambda", "<lambda>", node)
        for owner, name in binders_of(node):
            if bindings.local_of(owner, name) is None:
                _gap("binder", name, node)
        return True

    walk_nodes(body, visit)
