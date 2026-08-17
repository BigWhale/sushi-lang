"""A diagnostic inside a method body carries the same evidence as one inside a function."""
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
    """CE2421's help must name `poke self` (#327) -- the spelling that works."""
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
    """CE2092 named a type the user DID write (found in PR 4)."""
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
