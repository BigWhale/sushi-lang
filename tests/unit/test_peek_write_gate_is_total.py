"""Every mutating method is rejected through a `peek` receiver. No member left out."""
from __future__ import annotations

import pytest

from sushi_lang.semantics.passes.borrow import MUTATING_METHODS


def _program(mode: str, param_type: str, call: str, setup: str = "") -> str:
    """A minimal program whose callee performs `call` through a `<mode>` parameter."""
    return (
        f"{setup}"
        f"fn touch({mode} {param_type} r) ~:\n"
        f"    {call}\n"
        f"    return Result.Ok(~)\n"
        f"\n"
        f"fn main() i32:\n"
        f"    return Result.Ok(0)\n"
    )


_HASHMAP = "use <collections/hashmap>\n\n"

# One case per member of `MUTATING_METHODS`: (parameter type, the call, extra source).
# The receiver type must genuinely have the method, or the typecheck pass rejects the call before
# the borrow checker ever runs and the case would prove nothing.
CASES = {
    "push":          ("i32[]", "r.push(9)", ""),
    "pop":           ("List@(i32)", "r.pop()", ""),
    "insert":        ("List@(i32)", "r.insert(0, 5)", ""),
    "remove":        ("List@(i32)", "r.remove(0)", ""),
    "clear":         ("List@(i32)", "r.clear()", ""),
    "reserve":       ("List@(i32)", "r.reserve(10)", ""),
    "shrink_to_fit": ("List@(i32)", "r.shrink_to_fit()", ""),
    "rehash":        ("HashMap@(i32, string)", "r.rehash()", _HASHMAP),
    "destroy":       ("List@(i32)", "r.destroy()", ""),
    "free":          ("HashMap@(i32, string)", "r.free()", _HASHMAP),
    "fill":          ("i32[]", "r.fill(7)", ""),
    "reverse":       ("i32[]", "r.reverse()", ""),
}


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def test_every_mutating_method_has_a_case():
    """A method added to the set without a case here is a hole in this gate."""
    assert set(CASES) == set(MUTATING_METHODS)


@pytest.mark.parametrize("method", sorted(CASES))
def test_mutating_method_through_peek_is_CE2408(analyze, method):
    param_type, call, setup = CASES[method]
    reporter = analyze(_program("peek", param_type, call, setup))
    assert "CE2408" in _codes(reporter), (
        f"`{call}` through a peek {param_type} was not rejected"
    )


@pytest.mark.parametrize("method", sorted(CASES))
def test_mutating_method_through_poke_is_allowed(analyze, method):
    """The mirror: a `poke` receiver is the supported way to mutate through a borrow."""
    param_type, call, setup = CASES[method]
    reporter = analyze(_program("poke", param_type, call, setup))
    assert "CE2408" not in _codes(reporter), (
        f"`{call}` through a poke {param_type} must stay legal"
    )


# The two write shapes that are not method calls. They share the one gate, so a
# regression in it shows up here as well as above.

def test_field_assignment_through_peek_is_CE2408(analyze):
    src = (
        "struct Counter:\n"
        "    i32 n\n"
        "\n"
        "fn bump(peek Counter c) ~:\n"
        "    c.n := 42\n"
        "    return Result.Ok(~)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2408" in _codes(analyze(src))


def test_poke_borrow_of_a_peek_reference_is_CE2408(analyze):
    """#307: a `poke` borrow of a `peek` parameter would upgrade the borrow."""
    src = (
        "fn bump(poke i32 x) ~:\n"
        "    x := x + 1\n"
        "    return Result.Ok(~)\n"
        "\n"
        "fn outer(peek i32 x) ~:\n"
        "    bump(poke x)\n"
        "    return Result.Ok(~)\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2408" in _codes(analyze(src))


def test_read_through_peek_is_not_rejected(analyze):
    """The gate must not reject a read: that is what a `peek` parameter is for."""
    src = (
        "fn read(peek i32[] a) i32:\n"
        "    return Result.Ok(a.len())\n"
        "\n"
        "fn main() i32:\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2408" not in _codes(analyze(src))
