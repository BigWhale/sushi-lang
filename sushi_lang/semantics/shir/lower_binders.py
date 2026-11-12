"""Binder lowering: a lambda, a destructure, an `expand` and their binders (docs/design/ir.md 7.4, 7.5).

The rows of a lambda, of a destructuring `let` and of an `expand` are in the one table
`LOWERING` of `lower_nodes.py`; this module holds their lowering. Each construct declares locals. A
local takes the `LocalId` that the scope pass recorded for its binder, and the body that
holds the construct lists it: the scope pass gives the locals of a lambda and of a
destructure from the counter of that body (ir.md 7.1).

A lambda is a `Lambda` in the body where it stands, and its body is a block of the same
`ShirBody`, lowered by the same lowerer, so its ids come from the same counter. The
captures are not in the node: the scope pass keeps them in `Bindings`, by name and local
(ruling Q8). A parameter is a `LambdaParam`, its local and its convention; the name, the
written type with no `peek` or `poke` and the span are on its `ShirLocal`. `ret` and `err`
are the types written after the closing pipe. An expression body is a block of one
`Return`, and `expr_body` keeps the written shape: the typecheck pass infers the return
type from an expression body, and a lambda with a channel returns that value in
`Result.Ok` (docs/design/closures.md, section 2).

A destructure `let (a, b) = v` is a `Destructure` of the value as written. A target is the
local of its binder, `Discard` for `_`, or `Nested`. The AST builder binds the whole value
to a hidden local too, but no SHIR node reads it, so it is not a local of the body:
SHIR-to-SLIR lowering makes the place of the value. Whether a destructure owns or borrows
its elements is an answer of the borrow pass.

A tuple rebind `(a, b) := v` is a `Destructure` too, with a `Place` for each place (ir.md
7.5). The AST builder splits it into a destructure into hidden binders and one rebind of
each place from its binder; lowering joins the split again, so the hidden binders are no
locals, and the type of each target is the type of its place.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, Optional, cast

from sushi_lang.internals import errors as er
from sushi_lang.semantics import ast as a
from sushi_lang.semantics.param_modes import param_mode
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.nodes import TypeRef
from sushi_lang.semantics.typesys import ReferenceType

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.lower_nodes import BodyLowerer


def written_param_type(ty: Optional[TypeRef]) -> Optional[TypeRef]:
    """The written type of a parameter with no `peek` or `poke`: the convention holds the mode."""
    return ty.referenced_type if isinstance(ty, ReferenceType) else ty


def lower_lambda(lw: BodyLowerer, node: a.Lambda) -> e.Lambda:
    """`|params| body`: the parameters, the written types, then the body as a block."""
    ident = lw.ident()
    params = [_param(lw, param) for param in node.params]
    captures = tuple(capture.local for capture in lw.bindings.captures_of(node) or ())
    expr_body = not isinstance(node.body, a.Block)
    with lw.loops_at(0):
        body = (lw.block(node.body) if isinstance(node.body, a.Block)
                else lw.returned(node.body, lw.span(node.body)))
    return e.Lambda(ident, lw.span(node), params, node.ret, node.err_type, body, expr_body,
                    captures)


def _param(lw: BodyLowerer, param: a.Param) -> e.LambdaParam:
    local = lw.binder(param, param.name)
    lw.declare(local, param.name, written_param_type(param.ty), param.name_span,
               param.type_span)
    return e.LambdaParam(local, param_mode(param).convention)


def lower_destructure(lw: BodyLowerer, node: a.Let) -> s.Destructure:
    """`let (a, b) = v`: the value as written, then a target for each element."""
    ident = lw.ident()
    value = lw.expr(node.value)
    return s.Destructure(ident, lw.span(node), _targets(lw, node.targets or []), value)


def _targets(lw: BodyLowerer, targets: list[a.DestructureTarget]) -> list[s.DestructureTarget]:
    return [_target(lw, target) for target in targets]


def _target(lw: BodyLowerer, target: a.DestructureTarget) -> s.DestructureTarget:
    if target.nested is not None:
        return s.Nested(_targets(lw, target.nested), lw.span(target))
    if target.name is None:
        return s.Discard()
    local = lw.binder(target, target.name)
    lw.declare(local, target.name, target.ty, target.name_span, target.type_span)
    return local


def lower_tuple_rebind(lw: BodyLowerer, node: a.Let, rebinds: list[a.Rebind]) -> s.Destructure:
    """`(a, b) := v`: the value as written, then the place of each target.

    `node` is the split destructure, and `rebinds` holds one rebind for each of its
    binders, in the order of the binders: the place of a binder is the target of its rebind.
    """
    ident = lw.ident()
    value = lw.expr(node.value)
    return s.Destructure(ident, lw.span(node), _places(lw, node.targets or [], iter(rebinds)),
                         value)


def _places(lw: BodyLowerer, targets: list[a.DestructureTarget],
            rebinds: Iterator[a.Rebind]) -> list[s.DestructureTarget]:
    return [_place(lw, target, rebinds) for target in targets]


def _place(lw: BodyLowerer, target: a.DestructureTarget,
           rebinds: Iterator[a.Rebind]) -> s.DestructureTarget:
    if target.nested is not None:
        return s.Nested(_places(lw, target.nested, rebinds), lw.span(target))
    rebind = next(rebinds)
    if getattr(rebind.value, "id", None) != target.name:
        er.raise_internal_error("CE0015", message="a rebind of a tuple rebind does not read "
                                                  "the binder of its place")
    return s.Place(lw.expr(rebind.target))


def lower_expand(lw: BodyLowerer, node: a.Expand) -> s.Expand:
    """`expand(a in args):` in a template check body: the binder, the pack, the body."""
    pack = getattr(node.iterable, "id", None)
    if not lw.template or not isinstance(pack, str):
        er.raise_internal_error("CE0015", message="an `expand` reached lowering outside the "
                                                  "body of a template check, or with no pack")
    ident = lw.ident()
    local = lw.binder(node, node.var)
    lw.declare(local, node.var, None, node.var_span)
    return s.Expand(ident, lw.span(node), local, cast(str, pack), lw.block(node.body))
