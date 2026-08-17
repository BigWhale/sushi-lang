"""A diagnostic inside a method body carries the same evidence as one inside a function.

There were TWO Pass 3 entry points for one concept -- `_check_function` for plain
functions and perk methods, `_check_extension` for extension methods -- and they set up
the borrow state differently. The divergence was unpinned by any
test, and it showed as missing evidence: an extension-method parameter was registered
WITHOUT its declaration span, so every relational diagnostic raised in an extension body
rendered at tier 2 -- the primary location and nothing else -- while the identical
program written as a plain function rendered at tier 3 with the note that explains it.

The second location is the half the user cannot deduce: CE2411 says "another owner keeps
this value", and the note is what says WHICH declaration made it a borrow.

Both entry points are now one `_check_callable`, so this file asserts the two forms
produce the same shape rather than asserting the extension form alone.
"""
from __future__ import annotations

import pytest


_EAT = (
    "fn eat(nom i32[] a) i32:\n"
    "    return Result.Ok(a.len())\n"
    "\n"
)

# The same body, once as a plain function and once as each method kind. Each hands a
# `peek` parameter to a `nom` one, which is CE2411 -- a relational error, so a note is
# mandatory.
FORMS = {
    "function": (
        _EAT +
        "fn swallow(peek i32[] a) i32:\n"
        "    return Result.Ok(eat(nom a)??)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    ),
    "extension_method": (
        _EAT +
        "struct Box:\n"
        "    i32 n\n"
        "\n"
        "extend Box swallow(peek i32[] a) i32:\n"
        "    return eat(nom a)??\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    ),
    "perk_method": (
        _EAT +
        "perk Swallower:\n"
        "    fn swallow(peek i32[] a) i32\n"
        "\n"
        "struct Box:\n"
        "    i32 n\n"
        "\n"
        "extend Box with Swallower:\n"
        "    fn swallow(peek i32[] a) i32:\n"
        "        return eat(nom a)??\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    ),
}


def _diagnostic(reporter, code: str):
    for item in reporter.items:
        if item.code == code:
            return item
    return None


@pytest.mark.parametrize("form", sorted(FORMS))
def test_consuming_a_reference_param_is_relational_in_every_callable(analyze, form):
    diag = _diagnostic(analyze(FORMS[form]), "CE2411")
    assert diag is not None, f"{form}: CE2411 was not reported"
    notes = [sub for sub in diag.sub if sub.kind == "note"]
    assert notes, f"{form}: CE2411 rendered with no second location (tier 2)"
    assert any(note.span is not None for note in notes), (
        f"{form}: the note carries no span, so it renders without a location block"
    )


def test_writing_through_self_names_the_future_feature(analyze):
    """CE2421's help must name `poke self` (#327) -- the spelling that works.

    The receiver of a modeless method stays a read-only borrow, and the help points at
    the mutable-receiver spelling the language now has: declare the method
    `(poke self, ...)` and the write reaches the caller.
    """
    src = (
        "struct Counter:\n"
        "    i32 n\n"
        "\n"
        "extend Counter bump() i32:\n"
        "    self.n := 42\n"
        "    return 1\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    diag = _diagnostic(analyze(src), "CE2421")
    assert diag is not None
    helps = [sub.message for sub in diag.sub if sub.kind == "help"]
    assert any("poke self" in message for message in helps), helps
    notes = [sub for sub in diag.sub if sub.kind == "note"]
    assert any(note.span is not None for note in notes), (
        "CE2421 must point at the receiver it cannot reach, like CE2414 does"
    )


def test_bare_forwarding_of_a_reference_param_explains_the_spelling(analyze):
    """CE2092 named a type the user DID write (found in PR 4).

    Pass 2 unwraps a reference-typed name at every mention, so forwarding `v` into a
    `fn(peek i32)` call reports "expected 'peek i32', got 'i32'" for a parameter
    declared `peek i32`. The rule is right -- a borrow is created at the USE site -- so
    the fix is the message: say how to spell it.
    """
    src = (
        "fn apply(fn(peek i32) -> i32 g, peek i32 v) i32:\n"
        "    return Result.Ok(g(v)??)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    diag = _diagnostic(analyze(src), "CE2092")
    assert diag is not None
    helps = [sub.message for sub in diag.sub if sub.kind == "help"]
    assert any("peek v" in message for message in helps), helps
