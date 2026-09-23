"""A `poke` binding under a match on a place rooted in a constant is ONE fault (#784).

CE2400 is the rule: a constant has no storage a `poke` may write. The borrow pass's
CE2404 ("no stable address") for a scrutinee that is not a bare name stands down when the
place's root is a constant that CE2400 refuses, so `match TONES[0]:` and `match BOX.s:`
each read one code, as `match TONE:` always did. A root that is a local keeps CE2404.
"""
from __future__ import annotations

import pytest

_PRELUDE = '''enum Shade:
    Dim(i32)
    Bright(i32)

struct Box:
    Shade s

const Shade TONE = Shade.Dim(1)
const Shade[2] TONES = [Shade.Dim(2), Shade.Bright(3)]
const Box BOX = Box(Shade.Dim(4))

fn make() Shade:
    return Result.Ok(Shade.Dim(5))

'''


def _program(setup: str, scrutinee: str, mode: str) -> str:
    use = "r := r + 1" if mode == "poke" else 'println("{r}")'
    return (_PRELUDE + "fn main() i32:\n" + setup
            + f"    match {scrutinee}:\n"
            + f"        Shade.Dim({mode} r) -> {use}\n"
            + '        Shade.Bright(_) -> println("bright")\n'
            + "    return Result.Ok(0)\n")


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items if item.code.startswith("CE")]


_LOCAL_BOX = "    let Box b = Box(Shade.Dim(6))\n"

CASES = [
    # (id, setup, scrutinee, mode, expected codes)
    ("poke-constant-name", "", "TONE", "poke", ["CE2400"]),
    ("poke-constant-element", "", "TONES[0]", "poke", ["CE2400"]),
    ("poke-constant-field", "", "BOX.s", "poke", ["CE2400"]),
    ("peek-constant-name", "", "TONE", "peek", []),
    ("poke-local-field", _LOCAL_BOX, "b.s", "poke", ["CE2404"]),
    ("poke-temporary", "", "make()??", "poke", []),
]


@pytest.mark.parametrize("setup,scrutinee,mode,expected",
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_one_fault_reads_one_code(analyze, setup, scrutinee, mode, expected):
    assert _codes(analyze(_program(setup, scrutinee, mode), name="m")) == expected
