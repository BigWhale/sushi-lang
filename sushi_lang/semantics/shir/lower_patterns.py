"""Match lowering: a `match`, its arms and each pattern (docs/design/ir.md 7.5, 7.8).

ONE table, `PATTERNS`, gives the lowering of each AST class that a pattern position holds,
keyed on the class (gate: `tests/unit/test_shir_lowering_is_one_table.py`). A bare name is
a `str` in the AST: `_` gives `Wildcard`, and any other name gives a `PatBinding` with the
mode `Bare`. `peek x` and `poke x` give `Ref`, and `nom x` gives `Nom`. `Own(poke x)` keeps
its mode in two fields of the AST, and in the one `PatBinding` that `Own` holds.

A pattern holds what the source says, and lowering has no types. A literal and the bounds
of a range keep the written constant: `typecheck` reads them at the type of the position
(ir.md 7.8). A bound is a literal and not an expression, so lowering does not call the
constant evaluator for it. An enum pattern names its enum by the written name, the alias
included (`geo.Sign`). Whether that name is an enum is a question for `typecheck`.

`Match.consumes` is the written `match nom r:`. Whether the match owns a temporary
scrutinee is an answer of `borrow`. An arm body that the source writes as one expression
is a block with one `ExprStmt`.

A binding takes the local that the scope pass recorded for it, keyed by the pattern that
holds it (ir.md 7.1). The alternatives of `|` that bind one name share one local, and the
body lists that local one time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional, TypeAlias, cast

from sushi_lang.internals.report import Span
from sushi_lang.semantics import ast as a
from sushi_lang.semantics.param_modes import borrow_mode
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import ShirLocal
from sushi_lang.semantics.shir.nodes import ShirBlock

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.lower_nodes import BodyLowerer

#: One position of a pattern after lowering.
Item: TypeAlias = p.ShirPattern | p.PatBinding


def lower_match(lw: BodyLowerer, node: a.Match) -> s.Match:
    """`match x:`: the scrutinee, then each arm in the written order."""
    ident = lw.ident()
    scrutinee = lw.expr(node.scrutinee)
    arms = [_arm(lw, arm) for arm in node.arms]
    return s.Match(ident, lw.span(node), scrutinee, arms, node.consumes_scrutinee)


def _arm(lw: BodyLowerer, arm: a.MatchArm) -> p.Arm:
    pattern = cast(p.ShirPattern, item(lw, arm.pattern, None))
    return p.Arm(pattern, _arm_body(lw, arm.body))


def _arm_body(lw: BodyLowerer, body: Any) -> ShirBlock:
    """A block body, or a block of one `ExprStmt` for a body written as one expression."""
    if isinstance(body, a.Block):
        return lw.block(body)
    ident = lw.ident()
    statement = s.ExprStmt(lw.ident(), lw.span(body), lw.expr(body))
    return ShirBlock(ident, lw.span(body), [statement])


def item(lw: BodyLowerer, node: Any, owner: Optional[a.Node]) -> Item:
    """One position of a pattern. `owner` is the pattern that holds it, or None for an arm."""
    return PATTERNS[type(node)](lw, node, owner)


def _items(lw: BodyLowerer, nodes: list[Any], owner: Optional[a.Node]) -> list[Item]:
    return [item(lw, node, owner) for node in nodes]


def _named(lw: BodyLowerer, name: str, owner: Optional[a.Node], mode: p.BindMode,
           loc: Span) -> Item:
    """`_`, or a binding of `name` with its local, which the body lists one time."""
    if name == "_":
        return p.Wildcard(lw.ident(), loc)
    local = lw.binder(owner, name)
    if all(known.id != local for known in lw.locals):
        lw.locals.append(ShirLocal(local, name, None, loc))
    return p.PatBinding(local, mode)


def _bare(lw: BodyLowerer, node: str, owner: Optional[a.Node]) -> Item:
    return _named(lw, node, owner, s.Bare(), lw.span(owner))


def _ref(lw: BodyLowerer, node: a.RefBinding, owner: Optional[a.Node]) -> Item:
    return _named(lw, node.name, owner, s.Ref(borrow_mode(node.mode)), lw.span(node))


def _nom(lw: BodyLowerer, node: a.NomBinding, owner: Optional[a.Node]) -> Item:
    return _named(lw, node.name, owner, p.Nom(), lw.span(node))


def _wildcard(lw: BodyLowerer, node: a.WildcardPattern, _owner: Optional[a.Node]) -> Item:
    return p.Wildcard(lw.ident(), lw.span(node))


def _literal(lw: BodyLowerer, node: a.LiteralPattern, _owner: Optional[a.Node]) -> Item:
    return p.Literal(lw.ident(), lw.span(node), _constant(node))


def _range(lw: BodyLowerer, node: a.RangePattern, _owner: Optional[a.Node]) -> Item:
    return p.IntRange(lw.ident(), lw.span(node), _constant(node.low), _constant(node.high),
                      node.inclusive)


def _constant(node: a.LiteralPattern) -> e.Constant:
    """The written value: a string after its escapes, or an integer with its spelling."""
    if isinstance(node.value, str):
        return e.StrConst(node.value)
    return e.IntConst(node.value, node.radix, node.display if node.is_byte else None)


def _variant(lw: BodyLowerer, node: a.Pattern, _owner: Optional[a.Node]) -> Item:
    """`E.V(items)`: the enum by its written name, behind its alias when it has one."""
    ident = lw.ident()
    written = node.enum_name if node.namespace is None else f"{node.namespace}.{node.enum_name}"
    return p.Variant(ident, lw.span(node), lw.decls.refer(written), node.variant_name,
                     _items(lw, node.bindings, node))


def _tuple(lw: BodyLowerer, node: a.TuplePattern, _owner: Optional[a.Node]) -> Item:
    ident = lw.ident()
    return p.Tuple(ident, lw.span(node), _items(lw, node.elements, node))


def _own(lw: BodyLowerer, node: a.OwnPattern, _owner: Optional[a.Node]) -> Item:
    """`Own(x)`. The mode of `Own(poke x)` is a field of the `Own` pattern in the AST."""
    ident = lw.ident()
    if node.inner_borrow is None:
        inner = item(lw, node.inner_pattern, node)
    else:
        mode = s.Ref(borrow_mode(node.inner_borrow))
        inner = _named(lw, cast(str, node.inner_pattern), node, mode, lw.span(node))
    return p.Own(ident, lw.span(node), inner)


def _or(lw: BodyLowerer, node: a.OrPattern, owner: Optional[a.Node]) -> Item:
    """`x | y`: each alternative in the position of the `|`, so its owner is the same."""
    ident = lw.ident()
    return p.Or(ident, lw.span(node), _items(lw, node.alternatives, owner))


LowerPattern: TypeAlias = Callable[["BodyLowerer", Any, Optional[a.Node]], Item]

#: The lowering of each AST class that a pattern position holds.
PATTERNS: dict[type, LowerPattern] = {
    str: _bare,
    a.RefBinding: _ref,
    a.NomBinding: _nom,
    a.WildcardPattern: _wildcard,
    a.LiteralPattern: _literal,
    a.RangePattern: _range,
    a.Pattern: _variant,
    a.TuplePattern: _tuple,
    a.OwnPattern: _own,
    a.OrPattern: _or,
}
