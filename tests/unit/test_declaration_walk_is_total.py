"""The gate on `declarations()`: one walk, and it sees every documentable node.

`documented()` yields the blocks that ARE attached; `declarations()` yields every
declaration, block or none, and `documented()` filters it. Two walks over one AST would
drift, and `tests/docs_sweep.py` reads the same walk (documentation.md S10, R22).

Twelve dataclasses in `semantics/ast.py` carry a `doc` field and are declarations. The
other two carriers are not: `Program` holds the unit block, which CW7006 reads on its
own, and `Block` holds a body-first block, which is lifted onto the declaration around
it. This module asserts that one source declaring all twelve yields all twelve, so a new
documentable node cannot be added without the walk seeing it.

Totality has TWO halves, and node KINDS are only the first. The second is the `Program`'s
LISTS: the `collect` pass re-files a generic extension and a generic perk implementation
into a list of their own, so a walk that reads the concrete list alone loses every
generic one and still yields each KIND (#631). The fixture is therefore run through
`collect`, exactly as every consumer of the walk sees it, and every list is measured by
name.
"""
from __future__ import annotations

import dataclasses


from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics import ast as ast_mod
from sushi_lang.semantics.ast_walk import declarations
from sushi_lang.semantics.passes.collect import CollectorPass
from sushi_lang.semantics.passes.docs import documented

# Every declaration kind at once. Nothing here is documented: the walk is about what is
# DECLARED, and a block would only prove the other walk.
EVERY_KIND = '''\
const i32 ANSWER = 42

var i32 counter = 0

struct Ship:
    i32 hull

struct Box@(T):
    T held

enum Mood:
    Happy
    Sad

perk Greet:
    fn greet(i32 n) i32

extend Ship with Greet:
    fn greet(i32 n) i32:
        return n

extend Box@(T) with Greet:
    fn greet(i32 n) i32:
        return n

unsafe external "C" as libc because "the walk needs an external block":
    fn abs(i32 n) i32 = "abs"

fn plain(i32 a) i32:
    return Result.Ok(a)

extend i32 squared() i32:
    return self * self

extend Box@(T) fallback(i32 n) i32:
    return n

fn main() i32:
    return Result.Ok(0)
'''

# The two carriers that are not declarations, and why.
NOT_DECLARATIONS = {"Program", "Block"}

# Every list `Program` holds that the walk must cover, by name. A generic target keeps a
# list of its own, and what is in it is as much a declaration of the unit as what is in
# the concrete list beside it.
DECLARATION_LISTS = frozenset({
    "constants", "structs", "enums", "perks", "functions",
    "extensions", "generic_extensions", "perk_impls", "generic_perk_impls",
    "externals",
})

# The lists that hold no declaration. A `use` declares nothing, and an orphan block
# documents nothing -- it carries its own diagnostic instead.
NON_DECLARATION_LISTS = frozenset({"uses", "orphan_docs"})


def _program(src: str):
    """The fixture as every consumer of the walk sees it: after the `collect` pass.

    `parse_to_ast` alone is the raw builder output, and `collect` is what MOVES a
    generic implementation into its own list. A gate that reads the builder's lists
    proves nothing about the lists the `docs` pass and the two fences read.
    """
    program, _tree = parse_to_ast(src)
    CollectorPass(Reporter(source=src, filename="main")).run(program, unit_name="main")
    return program


def _doc_carrying_dataclasses() -> set:
    """Every dataclass in `semantics/ast.py` with a `doc` field."""
    found = set()
    for name in dir(ast_mod):
        node = getattr(ast_mod, name)
        if not (isinstance(node, type) and dataclasses.is_dataclass(node)):
            continue
        if any(field.name == "doc" for field in dataclasses.fields(node)):
            found.add(node.__name__)
    return found


def _list_fields(program) -> set:
    """Every field of `Program` that holds a list."""
    return {field.name for field in dataclasses.fields(program)
            if isinstance(getattr(program, field.name, None), list)}


def test_every_doc_carrying_declaration_is_yielded():
    carriers = _doc_carrying_dataclasses() - NOT_DECLARATIONS
    yielded = {type(node).__name__ for _kind, node in declarations(_program(EVERY_KIND))}
    assert carriers - yielded == set(), (
        f"declarations() never yields {sorted(carriers - yielded)}. A node that carries "
        "a doc block is a declaration the lint must be able to ask about."
    )


def test_every_list_of_the_program_is_classified():
    """A new list on `Program` needs a decision: is it declarations, or is it not?"""
    program = _program(EVERY_KIND)
    assert _list_fields(program) == DECLARATION_LISTS | NON_DECLARATION_LISTS


def test_every_declaration_list_is_walked():
    """Totality over the LISTS, and not over the node kinds alone (#631).

    The fixture fills every one of them, so an empty list here is the fixture going
    stale rather than the walk being complete.
    """
    program = _program(EVERY_KIND)
    walked = {id(node) for _kind, node in declarations(program)}
    for name in sorted(DECLARATION_LISTS):
        held = getattr(program, name)
        assert held, f"the fixture declares nothing in `{name}`"
        missing = [node for node in held if id(node) not in walked]
        assert not missing, (
            f"declarations() never yields what `{name}` holds. Every consumer of the "
            "walk -- the doc lints, the leak fence, the ptr quarantine -- loses it."
        )


def test_the_two_non_declaration_carriers_stay_out():
    yielded = {type(node).__name__ for _kind, node in declarations(_program(EVERY_KIND))}
    assert yielded & NOT_DECLARATIONS == set()


def test_every_yield_carries_a_kind_name():
    for kind, node in declarations(_program(EVERY_KIND)):
        assert isinstance(kind, str) and kind, f"{type(node).__name__} yields no kind"


def test_documented_is_a_filter_of_the_walk():
    """Same source, both walks: every documented owner is one the other walk yields."""
    src = "##: Unit. :##\n\n##: The answer. :##\nconst i32 ANSWER = 42\n\n" + EVERY_KIND
    program = _program(src)
    walked = {id(node) for _kind, node in declarations(program)}
    for _doc, owner in documented(program):
        if owner is None:
            continue          # the unit block documents no declaration
        assert id(owner) in walked


def test_the_walk_keeps_the_order_documented_uses():
    """R34: the sweep numbers its generated helpers from this order, so it is fixed."""
    src = "\n".join(f"##: Block {n}. :##\n{decl}" for n, decl in enumerate([
        "const i32 ANSWER = 42",
        "struct Ship:\n    i32 hull",
        "enum Mood:\n    Happy",
        "perk Greet:\n    fn greet(i32 n) i32",
        "fn plain(i32 a) i32:\n    return Result.Ok(a)",
    ])) + "\n\nfn main() i32:\n    return Result.Ok(0)\n"
    program = _program(src)
    from_walk = [node for _kind, node in declarations(program)
                 if getattr(node, "doc", None) is not None]
    from_documented = [owner for _doc, owner in documented(program) if owner is not None]
    assert [id(node) for node in from_walk] == [id(node) for node in from_documented]
