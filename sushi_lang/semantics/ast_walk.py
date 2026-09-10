"""One walk over a unit's declarations, shared by everything that needs the list.

`declarations()` is the single answer to "what does this unit declare, and what word does
a diagnostic call each kind by". The `docs` pass reads it to check blocks, the visibility
collector reads it to record who may name what, and `tests/docs_sweep.py` reads it to
number its generated examples. Two walks would drift, and a kind missing from the walk
would be silently missing from every consumer.

A GENERIC target keeps a list of its own: the `collect` pass re-files
`extend Box@(T)` and `extend Box@(T) with P` out of the concrete list, because every
later walk over that one assumes a concrete `self`. Each walk here therefore reads BOTH
lists, or a generic declaration is silently missing from every consumer (#631).

The ORDER is part of the contract, not an implementation detail --
`tests/docs_sweep.py` numbers its `doc_example_<n>` helpers from it, so a rearrangement
renames every one of them. `tests/unit/test_declaration_walk_is_total.py` is the gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, List, Optional, Tuple

from sushi_lang.semantics.ast import VarDef

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Program

# A declaration, and the word a diagnostic calls its kind by.
Declaration = Tuple[str, object]


# Every slot a declared type can sit in. Two facts decide whether a rule polices a
# position -- WHAT declares it and WHERE in the declaration it sits -- and a rule that
# conflates them gets one of them wrong. `RECEIVER` is the extension or perk-implementation
# target type; it is a position because the `ptr` rule exempts it and a leak rule does not.
POSITIONS = frozenset({
    "type", "receiver", "return", "error", "parameter", "field", "variant",
})


@dataclass(frozen=True)
class TypeSite:
    """One place a declaration's SIGNATURE names a type, and where to point at it.

    `decl` is the TOP-LEVEL declaration, never the field or the method inside it, because
    that is what visibility comes from: a field is as visible as its struct and a perk
    method as its perk or its target type. `kind` is the `declarations()` word for what
    declares the position, `position` is the slot within it, and `span` locates the slot.
    """

    kind: str
    position: str
    decl: Any
    ty: Optional[Any]
    span: Optional[Any]
    # The inner node, when the position belongs to one: the method, the field, the
    # variant. A diagnostic names THIS, while visibility comes from `decl`.
    at: Optional[Any] = None


def _bodied_kinds(program: 'Program') -> Iterator[Declaration]:
    """Every declaration with a body, with the word a diagnostic calls it by.

    A body is what lets a declaration hold two blocks, one above it and one first
    inside it, and it is why these come last in both walks.
    """
    for func in program.functions:
        yield "function", func
    for extension in [*program.extensions, *program.generic_extensions]:
        yield "extension", extension
    for impl in [*program.perk_impls, *program.generic_perk_impls]:
        for method in impl.methods:
            yield "perk method", method


def bodied(program: 'Program') -> List:
    """Every declaration with a body, in the one order both walks use."""
    return [node for _kind, node in _bodied_kinds(program)]


def declarations(program: 'Program') -> Iterator[Declaration]:
    """Every declaration of one unit, block or none, with the word for its kind.

    The unit block is not here: it documents no declaration, and `Program` is not one.
    Nor is a body-first block, which the builders lift onto the declaration around it.
    """
    for const in program.constants:
        yield ("variable" if isinstance(const, VarDef) else "constant"), const
    for struct in program.structs:
        yield "struct", struct
        for field in struct.fields:
            yield "field", field
    for enum in program.enums:
        yield "enum", enum
        for variant in enum.variants:
            yield "variant", variant
    for perk in program.perks:
        yield "perk", perk
        for method in perk.methods:
            yield "perk method", method
    for impl in [*program.perk_impls, *program.generic_perk_impls]:
        yield "perk implementation", impl
    for block in program.externals:
        yield "external block", block
        for decl in block.decls:
            yield "external declaration", decl
    yield from _bodied_kinds(program)


@dataclass(frozen=True)
class ConstraintSite:
    """One perk a declaration names in a type-parameter constraint.

    Not a `TypeSite`: a constraint names a PERK, and a perk is not a type. `decl` is the
    declaration whose visibility decides whether the constraint leaks (CE3010).
    """

    kind: str
    decl: Any
    perk_name: str
    span: Optional[Any]
    # The alias the constraint was written behind, when it was written behind one.
    # A qualified name never enters the flat scope, so a rule that measures the bare
    # name against that scope has to know which shape it is reading.
    namespace: Optional[str] = None


def signature_constraints(program: 'Program') -> Iterator[ConstraintSite]:
    """Every perk one unit's declarations name in a `@(T: P)` constraint.

    A separate walk from `signature_types` for one reason: a constraint carries a name and
    not a type, so a rule over it reads a different field. Both walks cover the same
    declarations.
    """
    for kind, decl in (
        *(("function", func) for func in program.functions),
        *(("struct", struct) for struct in program.structs),
        *(("enum", enum) for enum in program.enums),
        *(("extension", ext) for ext in
          [*program.extensions, *program.generic_extensions]),
    ):
        fallback = getattr(decl, "name_span", None) or getattr(decl, "loc", None)
        for param in getattr(decl, "type_params", None) or ():
            constraints = getattr(param, "constraints", None) or ()
            namespaces = getattr(param, "constraint_namespaces", None) or ()
            for index, constraint in enumerate(constraints):
                if isinstance(constraint, str):
                    yield ConstraintSite(
                        kind, decl, constraint,
                        getattr(param, "loc", None) or fallback,
                        namespaces[index] if index < len(namespaces) else None)


def _callable_sites(kind: str, decl: Any, callable_node: Any) -> Iterator[TypeSite]:
    """The return, the error arm and every parameter of one callable.

    `decl` and `callable_node` are the same object for a free function, and differ for a
    method: the signature is the method's, the visibility is its perk's or its target's.
    """
    fallback = (getattr(callable_node, "name_span", None)
                or getattr(callable_node, "loc", None))
    yield TypeSite(kind, "return", decl, getattr(callable_node, "ret", None),
                   getattr(callable_node, "ret_span", None) or fallback, callable_node)
    yield TypeSite(kind, "error", decl, getattr(callable_node, "err_type", None),
                   fallback, callable_node)
    for param in getattr(callable_node, "params", ()) or ():
        yield TypeSite(kind, "parameter", decl, getattr(param, "ty", None),
                       getattr(param, "type_span", None) or fallback, callable_node)


def signature_types(program: 'Program') -> Iterator[TypeSite]:
    """Every type one unit's declarations name in a SIGNATURE. Never in a body.

    The signature is where a type crosses a boundary, so this is the walk both fences over
    declared types run on: the `ptr` quarantine (CE5009), the public-signature rules
    (CE5008, CE3009), and nothing that cares about a local.

    A body is deliberately absent. A private type is perfectly legal in a local variable,
    and CE5009 -- which does care -- reads `body_types()` beside this one.
    """
    for const in program.constants:
        yield TypeSite("variable" if isinstance(const, VarDef) else "constant",
                       "type", const, getattr(const, "ty", None),
                       getattr(const, "type_span", None) or const.loc)

    for struct in program.structs:
        for field in struct.fields:
            yield TypeSite("struct", "field", struct, getattr(field, "ty", None),
                           getattr(field, "loc", None)
                           or getattr(struct, "name_span", None) or struct.loc, field)

    for enum in program.enums:
        for variant in enum.variants:
            span = (getattr(variant, "name_span", None) or getattr(variant, "loc", None)
                    or getattr(enum, "name_span", None) or enum.loc)
            for ty in getattr(variant, "associated_types", ()) or ():
                yield TypeSite("enum", "variant", enum, ty, span, variant)

    for perk in program.perks:
        for method in perk.methods:
            yield from _callable_sites("perk method", perk, method)

    for impl in [*program.perk_impls, *program.generic_perk_impls]:
        yield TypeSite("perk implementation", "receiver", impl,
                       getattr(impl, "target_type", None),
                       getattr(impl, "target_type_span", None) or impl.loc)
        for method in impl.methods:
            yield from _callable_sites("perk method", impl, method)

    for func in program.functions:
        yield from _callable_sites("function", func, func)

    for ext in [*program.extensions, *program.generic_extensions]:
        yield TypeSite("extension", "receiver", ext, getattr(ext, "target_type", None),
                       getattr(ext, "target_type_span", None) or ext.loc)
        yield from _callable_sites("extension", ext, ext)


# A node that holds no type and no sub-expression. Named rather than implied, so the
# gate can tell a deliberate leaf from a forgotten arm.
TERMINAL_NODES = frozenset({
    "Break", "Continue",
    "Name", "IntLit", "FloatLit", "BoolLit", "BlankLit", "StringLit",
    "DynamicArrayNew",
})


def _lambda_types(lam) -> Iterator[Tuple[Any, Any]]:
    """The types a lambda literal names: its parameters, its return, its error arm."""
    for param in lam.params or ():
        yield param.ty, param.type_span or param.loc or lam.loc
    yield lam.ret, lam.loc
    yield lam.err_type, lam.loc
    yield from _body_of(lam.body)


def _call_type_args(call) -> Iterator[Tuple[Any, Any]]:
    """Explicit call-site type arguments: `identity@(ptr)(p)` names `ptr`."""
    for ty in call.type_args or ():
        yield ty, call.type_args_loc or call.loc


def _array_literal_types(literal) -> Iterator[Tuple[Any, Any]]:
    """An array literal's elements, each with its optional repeat count."""
    for element in literal.elements:
        yield from _expr_types(element.value)
        yield from _expr_types(element.count)


def _expr_types(expr) -> Iterator[Tuple[Any, Any]]:
    """Every type an expression NAMES, and where to point at it.

    Total over the `Expr` union. A kind with nothing to walk is in `TERMINAL_NODES`;
    `tests/unit/test_body_walk_is_total.py` is the gate.
    """
    from sushi_lang.semantics import ast as a

    if expr is None:
        return
    match expr:
        case a.Name() | a.IntLit() | a.FloatLit() | a.BoolLit() | a.BlankLit() \
                | a.StringLit() | a.DynamicArrayNew():
            return
        case a.InterpolatedString():
            for part in expr.parts:
                if not isinstance(part, str):
                    yield from _expr_types(part)
        case a.ArrayLiteral():
            yield from _array_literal_types(expr)
        case a.DynamicArrayFrom():
            yield from _array_literal_types(expr.elements)
        case a.IndexAccess():
            yield from _expr_types(expr.array)
            yield from _expr_types(expr.index)
        case a.UnaryOp():
            yield from _expr_types(expr.expr)
        case a.BinaryOp():
            yield from _expr_types(expr.left)
            yield from _expr_types(expr.right)
        case a.RangeExpr():
            yield from _expr_types(expr.start)
            yield from _expr_types(expr.end)
        case a.Call():
            yield from _call_type_args(expr)
            yield from _expr_types(expr.callee)
            for arg in expr.args:
                yield from _expr_types(arg)
        case a.DotCall():
            yield from _call_type_args(expr)
            yield from _expr_types(expr.receiver)
            for arg in expr.args:
                yield from _expr_types(arg)
        case a.MethodCall():
            yield from _expr_types(expr.receiver)
            for arg in expr.args:
                yield from _expr_types(arg)
        case a.MemberAccess():
            yield from _expr_types(expr.receiver)
        case a.EnumConstructor():
            for arg in expr.args:
                yield from _expr_types(arg)
        case a.CastExpr():
            yield expr.target_type, expr.loc
            yield from _expr_types(expr.expr)
        case a.Borrow() | a.TryExpr():
            yield from _expr_types(expr.expr)
        case a.Spread():
            yield from _expr_types(expr.value)
        case a.Lambda():
            yield from _lambda_types(expr)


def _stmt_types(stmt) -> Iterator[Tuple[Any, Any]]:
    """Every type one statement NAMES, and where to point at it.

    Total over the `Stmt` subclasses; see `_expr_types` for the gate.
    """
    from sushi_lang.semantics import ast as a

    match stmt:
        case a.Break() | a.Continue():
            return
        case a.Let():
            yield stmt.ty, stmt.type_span or stmt.loc
            yield from _expr_types(stmt.value)
        case a.Rebind():
            yield from _expr_types(stmt.target)
            yield from _expr_types(stmt.value)
        case a.ExprStmt():
            yield from _expr_types(stmt.expr)
        case a.Return() | a.Print() | a.PrintLn():
            yield from _expr_types(stmt.value)
        case a.If():
            for cond, block in stmt.arms:
                yield from _expr_types(cond)
                yield from _body_of(block)
            yield from _body_of(stmt.else_block)
        case a.While():
            yield from _expr_types(stmt.cond)
            yield from _body_of(stmt.body)
        case a.Foreach():
            yield stmt.item_type, stmt.item_type_span or stmt.loc
            yield from _expr_types(stmt.iterable)
            yield from _body_of(stmt.body)
        case a.Expand():
            yield from _expr_types(stmt.iterable)
            yield from _body_of(stmt.body)
        case a.Match():
            yield from _expr_types(stmt.scrutinee)
            for arm in stmt.arms:
                yield from _body_of(arm.body)


def _body_of(body) -> Iterator[Tuple[Any, Any]]:
    """A body slot, which holds a block or a single expression."""
    from sushi_lang.semantics.ast import Block

    if body is None:
        return
    if isinstance(body, Block):
        for stmt in body.statements:
            yield from _stmt_types(stmt)
    else:
        yield from _expr_types(body)


def body_types(program: 'Program') -> Iterator[Tuple[Any, Any]]:
    """Every type one unit's BODIES name, as (type, span). Never a signature.

    The companion to `signature_types()`: a signature is where a type crosses a boundary,
    a body is where it is merely spelled. Only a rule about the SPELLING reads this one --
    the `ptr` quarantine (CE5009), which refuses `ptr` anywhere in a unit that declares no
    danger zone. A private type in a local is perfectly legal, so the leak fence does not.

    A slot the source left blank yields `(None, span)`; the caller filters.
    `tests/unit/test_body_walk_is_total.py` is the gate.
    """
    for node in bodied(program):
        yield from _body_of(getattr(node, "body", None))
