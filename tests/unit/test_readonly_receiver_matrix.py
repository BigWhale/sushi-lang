"""Every read-only receiver rejects every write shape, through ONE gate.

The language has five receivers a write cannot reach through, each found as its own bug
and each given its own code, because each carries its own escape:

  a match/foreach binding    -> CE2414 (#253) -- compiled as a private deep copy
  a `peek` reference        -> CE2408 (#302, R1) -- a read-only borrow of the caller
  the method receiver        -> CE2421 (#326) -- a borrow, per the #298 ruling
  a by-value method PARAM    -> CE2422 -- the same borrow, one line over
  a `let`-borrow binding     -> CE2426 (#344) -- shares the OWNER's data, so the write is
                                not merely lost: a reallocating one is a double free

and three shapes the write comes in, each of which had to be found separately for the
first two kinds:

  a mutating method under the receiver   `x.items.push(9)`
  a field assignment under the receiver  `x.n := 42`
  a `poke` borrow of the receiver       `f(poke x)`

Fifteen cells. The gate is one dispatcher (`_reject_readonly_write`) over a table of
kinds, called from the four write sites, so a new kind is one table entry and a new write
shape is one call -- never a fifth copy of the same walk. This test is what makes the
table load-bearing: a kind wired into the table but missing from a call site, or a call
site that forgets the dispatcher, turns a cell red instead of shipping a silent write.

`test_peek_write_gate_is_total.py` covers the other axis for the `peek` kind: every
member of `_MUTATING_METHODS`, not every shape.
"""
from __future__ import annotations

import pytest


# Each kind builds a program whose method body performs `write` on a receiver of that
# kind. `{write}` is substituted with a statement using the receiver name `r`.
_GROW = (
    "fn grow(poke i32[] arr) ~:\n"
    "    arr.push(9)\n"
    "    return Result.Ok(~)\n"
    "\n"
)

_SHAPES = {
    "mutating_method": "r.items.push(9)",
    "field_assign":    "r.n := 42",
    "poke_borrow":     "grow(poke r.items)",
}

# The `poke` borrow shape for a whole-receiver borrow needs a matching callee, so each
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
        "fn touch(peek Holder r) ~:\n"
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


def _method_param_program(write: str) -> str:
    """A by-value parameter of a method: the receiver's rule, one line over.

    The extended type is deliberately NOT `Holder`, so nothing here can be satisfied by
    the receiver arm -- the write is on `r`, an ordinary parameter.
    """
    return (
        _STRUCT + _GROW +
        "struct Box:\n"
        "    i32 v\n"
        "\n"
        "extend Box touch(Holder r) i32:\n"
        f"    {write}\n"
        "    return 1\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )


def _let_borrow_program(write: str) -> str:
    """A `let` bound from a read THROUGH a live owner (#344).

    The kind the table was missing. Unlike the pattern binding above, the binding shares
    the owner's heap rather than copying it, so the same write that is merely lost there
    frees the owner's buffer here as soon as it reallocates.
    """
    return (
        _STRUCT + _GROW +
        "struct Outer:\n"
        "    Holder inner\n"
        "\n"
        "fn main() i32:\n"
        "    let Outer o = Outer(Holder(1, from([1, 2])))\n"
        "    let Holder r = o.inner\n"
        f"    {write}\n"
        "    return Result.Ok(0)\n"
    )


KINDS = {
    "peek_reference":  ("CE2408", _peek_program),
    "pattern_binding": ("CE2414", _binding_program),
    "method_receiver": ("CE2421", _self_program),
    "method_parameter": ("CE2422", _method_param_program),
    "let_borrow": ("CE2426", _let_borrow_program),
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
    """The receiver itself handed to a `poke` parameter -- the shape #307 found."""
    src = (
        _STRUCT +
        "fn bump(poke Holder h) ~:\n"
        "    h.n := h.n + 1\n"
        "    return Result.Ok(~)\n"
        "\n"
        "extend Holder inc() i32:\n"
        "    bump(poke self)\n"
        "    return 1\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2421" in _codes(analyze(src))


def test_poke_borrow_of_a_whole_method_parameter_is_rejected(analyze):
    """The parameter twin of the shape above."""
    src = (
        _STRUCT +
        "struct Box:\n"
        "    i32 v\n"
        "\n"
        "fn bump(poke Holder h) ~:\n"
        "    h.n := h.n + 1\n"
        "    return Result.Ok(~)\n"
        "\n"
        "extend Box inc(Holder r) i32:\n"
        "    bump(poke r)\n"
        "    return 1\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2422" in _codes(analyze(src))


def test_a_poke_method_parameter_stays_writable(analyze):
    """The escape CE2422 names, and the line that keeps the gate off `poke`.

    A `poke` parameter is the supported way to write through a method's argument, so the
    method-parameter kind must exclude reference parameters entirely -- a `peek` one is
    already CE2408, and a `poke` one is the answer.
    """
    src = (
        _STRUCT +
        "struct Box:\n"
        "    i32 v\n"
        "\n"
        "extend Box inc(poke Holder r) i32:\n"
        "    r.n := 42\n"
        "    r.items.push(9)\n"
        "    return 1\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(src))
    assert "CE2422" not in codes and "CE2408" not in codes, codes


def test_poke_borrow_of_a_whole_let_borrow_binding_is_rejected(analyze):
    """The binding itself handed to a `poke` parameter -- the third shape of #344."""
    src = (
        _STRUCT + _GROW +
        "fn main() i32:\n"
        "    let Holder h = Holder(1, from([1, 2]))\n"
        "    let i32[] v = h.items\n"
        "    grow(poke v)\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2426" in _codes(analyze(src))


def test_destroy_through_a_let_borrow_binding_is_rejected(analyze):
    """`.destroy()` releases the OWNER's storage: a double free, not a lost write."""
    src = (
        _STRUCT +
        "fn main() i32:\n"
        "    let Holder h = Holder(1, from([1, 2]))\n"
        "    let i32[] v = h.items\n"
        "    v.destroy()\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2426" in _codes(analyze(src))


def test_a_let_borrow_out_of_a_temporary_keeps_its_own_code(analyze):
    """An owner with no BorrowState is still an owner, and its buffer is still real.

    The reason the row keys on `is_let_borrow` rather than on `borrows_from is not None`:
    a temporary records no owner NAME, and the `borrows_from` spelling would hand this
    case to the CE2414 row, which tells the user their `let` is a match binding.
    """
    src = (
        _STRUCT +
        "fn make() Holder:\n"
        "    return Result.Ok(Holder(1, from([1, 2])))\n"
        "\n"
        "fn f() i32:\n"
        "    let i32[] v = make()??.items\n"
        "    v.push(9)\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(src))
    assert "CE2426" in codes and "CE2414" not in codes, codes


def test_a_rebound_let_borrow_becomes_writable(analyze):
    """A rebind RE-INITIALIZES: the new value is the binding's own, so writes are legal.

    The `is_let_borrow` twin of `test_rebind_of_a_borrowed_binding_makes_it_an_owner`.
    Without the clear in `_reinitialize`, this is a false CE2426.
    """
    src = (
        _STRUCT +
        "fn main() i32:\n"
        "    let Holder h = Holder(1, from([1, 2]))\n"
        "    let i32[] v = h.items\n"
        "    v := from([3, 4])\n"
        "    v.push(9)\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2426" not in _codes(analyze(src))


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
