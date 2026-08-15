"""Every read-only receiver rejects every write shape, through ONE gate.

The language now has three receivers a write cannot reach through, each found as its own
bug and each given its own code:

  a match/foreach binding  -> CE2414 (#253) -- compiled as a private deep copy
  a `&peek` reference      -> CE2408 (#302, R1) -- a read-only borrow of the caller
  the method receiver      -> CE2421 (#326) -- a borrow, per the #298 ruling

and three shapes the write comes in, each of which had to be found separately for the
first two kinds:

  a mutating method under the receiver   `x.items.push(9)`
  a field assignment under the receiver  `x.n := 42`
  a `&poke` borrow of the receiver       `f(&poke x)`

Nine cells. The gate is one dispatcher (`_reject_readonly_write`) over a table of kinds,
called from the four write sites, so a new kind is one table entry and a new write shape
is one call -- never a third copy of the same walk. This test is what makes the table
load-bearing: a kind wired into the table but missing from a call site, or a call site
that forgets the dispatcher, turns a cell red instead of shipping a silent write.

`test_peek_write_gate_is_total.py` covers the other axis for the `&peek` kind: every
member of `_MUTATING_METHODS`, not every shape.
"""
from __future__ import annotations

import pytest


# Each kind builds a program whose method body performs `write` on a receiver of that
# kind. `{write}` is substituted with a statement using the receiver name `r`.
_GROW = (
    "fn grow(&poke i32[] arr) ~:\n"
    "    arr.push(9)\n"
    "    return Result.Ok(~)\n"
    "\n"
)

_SHAPES = {
    "mutating_method": "r.items.push(9)",
    "field_assign":    "r.n := 42",
    "poke_borrow":     "grow(&poke r.items)",
}

# The `&poke` borrow shape for a whole-receiver borrow needs a matching callee, so each
# kind carries its own spelling of it below where the field form does not fit.
_STRUCT = (
    "struct Holder:\n"
    "    i32 n\n"
    "    i32[] items\n"
    "\n"
)


def _peek_program(write: str) -> str:
    return (
        _STRUCT + _GROW +
        "fn touch(&peek Holder r) ~:\n"
        f"    {write}\n"
        "    return Result.Ok(~)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )


def _binding_program(write: str) -> str:
    return (
        _STRUCT + _GROW +
        "enum Wrapped:\n"
        "    One(Holder)\n"
        "\n"
        "fn main() i32:\n"
        "    let Wrapped w = Wrapped.One(Holder(1, from([1, 2])))\n"
        "    match w:\n"
        "        Wrapped.One(r) ->\n"
        f"            {write}\n"
        "    return Result.Ok(0)\n"
    )


def _self_program(write: str) -> str:
    return (
        _STRUCT + _GROW +
        "extend Holder touch() i32:\n"
        f"    {write.replace('r.', 'self.')}\n"
        "    return 1\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )


KINDS = {
    "peek_reference":  ("CE2408", _peek_program),
    "pattern_binding": ("CE2414", _binding_program),
    "method_receiver": ("CE2421", _self_program),
}


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_write_through_readonly_receiver_is_rejected(analyze, kind, shape):
    code, build = KINDS[kind]
    reporter = analyze(build(_SHAPES[shape]))
    assert code in _codes(reporter), (
        f"`{_SHAPES[shape]}` through a {kind} receiver was not rejected with {code}; "
        f"got {_codes(reporter)}"
    )


def test_poke_borrow_of_the_whole_receiver_is_rejected(analyze):
    """The receiver itself handed to a `&poke` parameter -- the shape #307 found."""
    src = (
        _STRUCT +
        "fn bump(&poke Holder h) ~:\n"
        "    h.n := h.n + 1\n"
        "    return Result.Ok(~)\n"
        "\n"
        "extend Holder inc() i32:\n"
        "    bump(&poke self)\n"
        "    return 1\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2421" in _codes(analyze(src))


def test_every_readonly_kind_is_in_the_gate_table(analyze):
    """A kind in the checker's table without a row here is a hole in this matrix."""
    from sushi_lang.semantics.passes.borrow import BorrowChecker

    table_codes = {kind.code.code for kind in BorrowChecker._READONLY_RECEIVERS}
    assert table_codes == {code for code, _ in KINDS.values()}


# The green mirror. Each kind must leave READS alone, or the gate is a ban on the
# receiver rather than a ban on writing through it.

@pytest.mark.parametrize("kind", sorted(KINDS))
def test_reads_through_a_readonly_receiver_stay_legal(analyze, kind):
    code, build = KINDS[kind]
    reporter = analyze(build("println(\"{r.items.len()} {r.n}\")"))
    assert code not in _codes(reporter)
