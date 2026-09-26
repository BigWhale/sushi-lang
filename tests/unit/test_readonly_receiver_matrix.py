"""Every read-only receiver rejects every write shape, through ONE gate."""
from __future__ import annotations



# Each kind builds a program whose method body performs `write` on a receiver of that
# kind. `{write}` is substituted with a statement using the receiver name `r`.
_GROW = (
    "fn grow(poke i32[] arr) ~:\n"
    "    arr.push(9)\n"
    "    return Result.Ok(~)\n"
    "\n"
)


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
    """A by-value parameter of a method: the receiver's rule, one line over."""
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
    """A `let` bound from a read THROUGH a live owner (#344)."""
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


def _function_param_program(write: str) -> str:
    """An unmarked parameter of a PLAIN function -- CE2422's kind, generalized."""
    return (
        _STRUCT + _GROW +
        "fn touch(Holder r) ~:\n"
        f"    {write}\n"
        "    return Result.Ok(~)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )


def _chained_program(write: str) -> str:
    """An unbound chained get-out -- the sixth kind, keyed on SHAPE, not state (#352)."""
    return (
        _STRUCT + _GROW +
        "fn main() i32:\n"
        "    let Own@(Holder) o = Own.alloc(Holder(1, from([1, 2])))\n"
        f"    {write.replace('r.', 'o.get().')}\n"
        "    o.destroy()\n"
        "    return Result.Ok(0)\n"
    )


KINDS = {
    "peek_reference":  ("CE2408", _peek_program),
    "pattern_binding": ("CE2414", _binding_program),
    "method_receiver": ("CE2421", _self_program),
    "method_parameter": ("CE2422", _method_param_program),
    "function_parameter": ("CE2422", _function_param_program),
    "let_borrow": ("CE2426", _let_borrow_program),
    "unbound_chained": ("CE2429", _chained_program),
}

# Cells whose code differs from the kind's: a `poke` borrow of a chained expression is
# rejected upstream as CE2404 (no stable address), which pre-dates and outranks the gate.


# The REBIND position (#590): a write to the NAME, not through it. It splits the kinds
# rather than adding a column, because the answer is not the same for all of them -- a
# name with storage of ITS OWN may be rebound, a name that is a view of another value's
# storage may not. `None` is "accepted"; the receiver's CE1002 comes from the scope pass,
# which refuses `self :=` before the borrow pass sees it.

























def test_every_readonly_kind_is_in_the_gate_table():
    """A kind in the checker's table without a row here is a hole in this matrix."""
    from sushi_lang.semantics.passes.borrow import READONLY_RECEIVERS

    table_codes = {kind.code.code for kind in READONLY_RECEIVERS}
    # CE2429 is the shape-keyed sixth kind (#352): it answers BEFORE the state table,
    # so it is deliberately not a table row.
    assert table_codes == {code for code, _ in KINDS.values()} - {"CE2429"}


# The green mirror. Each kind must leave READS alone, or the gate is a ban on the
# receiver rather than a ban on writing through it.

