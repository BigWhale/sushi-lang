"""One walk over a unit's declarations, shared by everything that needs the list.

`declarations()` is the single answer to "what does this unit declare, and what word does
a diagnostic call each kind by". The `docs` pass reads it to check blocks, the visibility
collector reads it to record who may name what, and `tests/docs_sweep.py` reads it to
number its generated examples. Two walks would drift, and a kind missing from the walk
would be silently missing from every consumer.

The ORDER is part of the contract, not an implementation detail --
`tests/docs_sweep.py` numbers its `doc_example_<n>` helpers from it, so a rearrangement
renames every one of them. `tests/unit/test_declaration_walk_is_total.py` is the gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, List, Optional, Tuple

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
    for impl in program.perk_impls:
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
        yield "constant", const
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
    for impl in program.perk_impls:
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
            for constraint in getattr(param, "constraints", None) or ():
                if isinstance(constraint, str):
                    yield ConstraintSite(kind, decl, constraint,
                                         getattr(param, "loc", None) or fallback)


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
    and CE5009 -- which does care -- keeps its own body walk.
    """
    for const in program.constants:
        yield TypeSite("constant", "type", const, getattr(const, "ty", None),
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

    for impl in program.perk_impls:
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
