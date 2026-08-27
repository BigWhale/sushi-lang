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

from typing import TYPE_CHECKING, Iterator, List, Tuple

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Program

# A declaration, and the word a diagnostic calls its kind by.
Declaration = Tuple[str, object]


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
