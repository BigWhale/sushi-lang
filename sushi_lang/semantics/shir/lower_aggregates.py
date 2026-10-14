"""Aggregate lowering: the constructions and the places (docs/design/ir.md 7.4, 7.10).

The rows of a struct, an enum, a tuple and an array construction, of a range, and of the
places that read into a value (a member read `x.m`, an index `a[i]` and a slice `a[i..j]`)
are in the one table `LOWERING` of `lower_nodes.py`; this module holds their lowering. A
call shape gives a construction through `_call` and `_dotted` there, and calls
`struct_init` and `enum_init` here.

What a written name before a dot denotes decides each node, and lowering reads it from
`Bindings` and from the declaration tables (`DeclLookup`), never from a type:

- A struct name as the callee is a `StructInit`. A named construction gives `fields` in
  the declaration order of the struct, and `written_order` keeps the order that the source
  writes, which is the order of evaluation (ruling R1). SHIR has no temporary for it:
  SHIR-to-SLIR lowering evaluates the fields in `written_order` where it lowers the
  expression. A named construction whose names are not the fields of a struct (an
  unknown, a repeated or a missing name), or whose name has no struct, keeps its fields
  in the written order and its written names in `names`: the program has an error, and
  the typecheck pass reports it (CE2080, CE2081, CE2082).
- `T.m(...)` and `T.m` on an enum `T` with a variant `m` are an `EnumInit`, also behind an
  alias. The declaration is the written name, the alias included (`geo.Sign`), as a pattern
  names it.
- `ns.m` on a namespace is the `ConstRef`, the `StorageRef` or the `GlobalRef` of what the
  namespace holds under `m`, with the qualified name as written. A function and a C global
  of an `unsafe external` block (`libc.environ`) are a `GlobalRef`. A type and a name that
  the namespace does not hold are holes.
- Any other `x.m` is a `Field` of `x`. Which field it is, and its index, are answers of the
  typecheck pass (ruling S5).
- `a[i]` is an `Index` with the index as written, and with the `unchecked` mark that the
  AST builder puts on an index in a `dont_panic` body (ir.md 4.9). A range in the index
  position is a slice (docs/design/slices.md): a `Slice` with the bounds and `inclusive` of
  the range. A slice with a reversed range is an error hole: the program always has an
  error (CE2122), and a `Slice` has no field for `.rev()`, so the hole never stops strict
  mode.

`from([...])` and `new()` are an `ArrayInit` of the kind `Dynamic`, and `[...]` one of the
kind `Fixed`, with a run for each element: `Single`, `Repeat` (the value, then the count:
ruling R2) and `RangeRun`. A range is a `Range` in each position, a run and a value alike.
The count of a repeat, the bounds of a range or a slice and an index record their value
when the compiler can read it (ruling E3).

Ruling E3 also decides which node is opaque. A field read, a member behind an alias, a
struct, a variant, a tuple and a dynamic array take their id from `made_opaque`: the
constant evaluator writes on the AST for a member access (it stamps `namespace_ref` and
folds an alias out of the receiver), and the value of a construction is never a number
that the typecheck pass reads. A fixed array is not opaque, because the evaluator reads a
fixed literal of constants with no write on the AST (`[4, 2][0]` as a divisor). An index
and a range are not opaque either, for the same reason. A slice is opaque: its value is a
view.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Sequence, cast

from sushi_lang.semantics import ast as a
from sushi_lang.semantics.array_runs import is_range_element
from sushi_lang.semantics.shir import bindings as bd
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.lookup import GLOBAL_KINDS, VARIABLE_KIND, DeclLookup
from sushi_lang.semantics.typesys import BuiltinType, UnknownType

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.lower_nodes import BodyLowerer

#: A type as written before a dot: a type name, or a type behind an alias.
WrittenType = BuiltinType | UnknownType


def written_name(ty: WrittenType) -> str:
    """The name of a type as the source writes it, with its alias (`geo.Sign`)."""
    namespace = ty.namespace if isinstance(ty, UnknownType) else None
    return str(ty) if namespace is None else f"{namespace}.{ty}"


# -- constructions ---------------------------------------------------------------------


def struct_init(lw: BodyLowerer, node: Any, written: str, declared: str, args: list[Any],
                names: Optional[list[str]]) -> e.ShirExpr:
    """`S(...)`, `S(f: ...)` or `ns.S(...)`: the struct `written`, which the table calls `declared`.

    A positional construction keeps its arguments as written. A named one puts them in the
    declaration order, and `written_order` keeps the order of evaluation (ruling R1). A
    named one whose names are not the fields of the struct keeps the written order and
    the written names.
    """
    positions = None if names is None else _declared_positions(lw.lookup, declared, names)
    ident = lw.made_opaque()
    written_args = lw.arguments(args)
    decl = lw.decls.refer(written)
    if positions is None:
        kept = None if names is None else tuple(names)
        return e.StructInit(ident, lw.span(node), decl, written_args, tuple(range(len(args))),
                            kept)
    fields = [arg for _position, arg in sorted(zip(positions, written_args, strict=True),
                                               key=lambda pair: pair[0])]
    return e.StructInit(ident, lw.span(node), decl, fields, tuple(positions), None)


def _declared_positions(lookup: Optional[DeclLookup], declared: str,
                        names: Sequence[str]) -> Optional[list[int]]:
    """The declaration position of each written name, or None when the names are not the
    fields of the struct: a name that it does not declare, a repeated or a missing one."""
    fields = None if lookup is None else lookup.field_names(declared)
    if fields is None or sorted(names) != sorted(fields):
        return None
    return [fields.index(name) for name in names]


def enum_init(lw: BodyLowerer, node: Any, ty: WrittenType, variant: str,
              args: list[Any]) -> e.EnumInit:
    """The variant `variant` of the enum that `ty` writes, with the payload as written."""
    ident = lw.made_opaque()
    return e.EnumInit(ident, lw.span(node), lw.decls.refer(written_name(ty)), variant,
                      lw.arguments(args))


def lower_tuple(lw: BodyLowerer, node: a.TupleLiteral) -> e.TupleInit:
    ident = lw.made_opaque()
    return e.TupleInit(ident, lw.span(node), [lw.expr(part) for part in node.elements])


# -- arrays ----------------------------------------------------------------------------


def lower_array(lw: BodyLowerer, node: a.ArrayLiteral) -> e.ArrayInit:
    """`[...]`: a fixed array."""
    ident = lw.ident()
    return e.ArrayInit(ident, lw.span(node), e.Fixed(), _runs(lw, node.elements))


def lower_from(lw: BodyLowerer, node: a.DynamicArrayFrom) -> e.ArrayInit:
    """`from([...])`: a dynamic array with the runs of its literal (ir.md 7.10)."""
    ident = lw.made_opaque()
    return e.ArrayInit(ident, lw.span(node), e.Dynamic(), _runs(lw, node.elements.elements))


def lower_new(lw: BodyLowerer, node: a.DynamicArrayNew) -> e.ArrayInit:
    """`new()`: a dynamic array with no run."""
    return e.ArrayInit(lw.made_opaque(), lw.span(node), e.Dynamic(), [])


def _runs(lw: BodyLowerer, elements: list[a.ArrayElement]) -> list[e.ArrayRun]:
    return [_run(lw, element) for element in elements]


def _run(lw: BodyLowerer, element: a.ArrayElement) -> e.ArrayRun:
    """One element. A repeat lowers its value first and then its count (ruling R2).

    A range with a count (`[0..3; 2]`) is a repeat as written, and the typecheck pass
    refuses it.
    """
    if element.count is not None:
        value = lw.expr(element.value)
        return e.Repeat(value, lw.operand(element.count, True))
    if is_range_element(element):
        return e.RangeRun(lower_range(lw, cast(a.RangeExpr, element.value)))
    return e.Single(lw.expr(element.value))


def lower_range(lw: BodyLowerer, node: a.RangeExpr) -> e.Range:
    """`a..b`, `a..=b` and `(a..b).rev()`, with the value of each bound that the compiler reads."""
    ident = lw.ident()
    start, end = _bounds(lw, node)
    return e.Range(ident, lw.span(node), start, end, node.inclusive, node.reverse)


def _bounds(lw: BodyLowerer, node: a.RangeExpr) -> tuple[e.ShirExpr, e.ShirExpr]:
    """The start and the end of a range or a slice, in this order (ruling E3)."""
    start = lw.operand(node.start, True)
    return start, lw.operand(node.end, True)


# -- an index and a slice --------------------------------------------------------------


def lower_index(lw: BodyLowerer, node: a.IndexAccess) -> e.ShirExpr:
    """`a[i]`, or the slice `a[i..j]` when the index is a range."""
    if node.is_slice:
        return _slice(lw, node, cast(a.RangeExpr, node.index))
    ident = lw.ident()
    base = lw.expr(node.array)
    return e.Index(ident, lw.span(node), base, lw.operand(node.index, True), node.unchecked)


def _slice(lw: BodyLowerer, node: a.IndexAccess, bounds: a.RangeExpr) -> e.ShirExpr:
    """`a[i..j]` and `a[i..=j]`: a borrowed view. A reversed range is an error hole (CE2122)."""
    if bounds.reverse:
        return lw.error_hole(node)
    ident = lw.made_opaque()
    base = lw.expr(node.array)
    start, end = _bounds(lw, bounds)
    return e.Slice(ident, lw.span(node), base, start, end, bounds.inclusive)


# -- a member read ---------------------------------------------------------------------


def lower_member(lw: BodyLowerer, node: a.MemberAccess) -> e.ShirExpr:
    """`x.m`: a member of a namespace, a unit variant of an enum, or a field of a value."""
    found = lw.bindings.resolution_of(node.receiver)
    if isinstance(found, bd.Namespace):
        return _namespace_member(lw, node, found.name)
    ty = lw.type_before_dot(node.receiver)
    if ty is not None:
        return _unit_variant(lw, node, ty)
    ident = lw.made_opaque()
    return e.Field(ident, lw.span(node), lw.expr(node.receiver), node.member)


def _namespace_member(lw: BodyLowerer, node: a.MemberAccess, alias: str) -> e.ShirExpr:
    """`ns.m`: what the namespace holds under `m`, by the qualified name as written."""
    lookup = lw.lookup
    kind = None if lookup is None else lookup.member_kind(lw.bindings.body, alias, node.member)
    loc = lw.span(node)
    written = f"{alias}.{node.member}"
    if kind == "constant":
        return e.ConstRef(lw.made_opaque(), loc, lw.decls.refer(written))
    if kind == VARIABLE_KIND:
        return e.StorageRef(lw.made_opaque(), loc, lw.decls.refer(written))
    if kind in GLOBAL_KINDS:
        return e.GlobalRef(lw.made_opaque(), loc, lw.decls.refer(written))
    return lw.hole(node)


def _unit_variant(lw: BodyLowerer, node: a.MemberAccess, ty: WrittenType) -> e.ShirExpr:
    """`T.V`: a variant with no payload. Any other member of a type is a hole."""
    if lw.lookup is None or not lw.lookup.is_variant(str(ty), node.member):
        return lw.hole(node)
    return enum_init(lw, node, ty, node.member, [])
