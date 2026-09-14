"""Every borrow position asks ONE gate whether the name has an address (#685).

CE2400 -- "a `const` has no address, so it cannot be borrowed" -- was written at five
sites with four predicates, and the fourth read the FLAT constant table. `by_name` is
first-wins over the whole program, so a unit whose own `var` shares a name with another
unit's `const` was refused, and a unit whose own `const` shares a name with another
unit's `var` was let through to write read-only memory. The multi-unit halves of that
measurement are fixtures under `tests/references/borrow_of_constant/`; the matrix below
is the single-unit half, plus the gate that keeps the emission in one place.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SEAM = SOURCE_ROOT / "semantics" / "constant_borrow.py"

_PRELUDE = '''struct Box:
    i32 n

enum Shade:
    Dim(i32)
    Bright(i32)

const i32 LIMIT = 10
const Shade TONE = Shade.Dim(2)
var i32 COUNT = 0
var Shade MOOD = Shade.Dim(2)

fn takes(poke i32 v) ~:
    v := v + 1
    return Result.Ok(~)

fn looks(peek i32 v) i32:
    return Result.Ok(v)

extend i32 bump(poke self) ~:
    self := self + 1

'''

# Each position writes the name `{n}`; `{e}` is an enum-typed name for the match arm.
_POSITIONS = {
    "poke_argument": "    takes(poke {n})\n",
    "peek_argument": "    let i32 seen = looks(peek {n})??\n    println(\"{{seen}}\")\n",
    "let_poke":      "    let poke i32 ref = {n}\n    ref := ref + 1\n",
    "let_peek":      "    let peek i32 ref = {n}\n    println(\"{{ref}}\")\n",
    "poke_self":     "    {n}.bump()\n",
    "match_poke":    ("    match {e}:\n"
                      "        Shade.Dim(poke d) -> d := d + 1\n"
                      "        Shade.Bright(_) -> println(\"bright\")\n"),
}

# kind -> (the extra local lines, the i32 name, the Shade name)
_KINDS = {
    "constant":      ("", "LIMIT", "TONE"),
    "unit_variable": ("", "COUNT", "MOOD"),
    "local":         ("    let i32 LIMIT = 4\n    let Shade TONE = Shade.Dim(2)\n",
                      "LIMIT", "TONE"),
}

# A constant is the only kind with no address. A `var` is storage in the data segment
# and a local is a frame slot, so neither may ever read CE2400.
_REFUSED = {"constant"}


def _program(position: str, kind: str) -> str:
    extra, name, enum_name = _KINDS[kind]
    body = _POSITIONS[position].format(n=name, e=enum_name)
    return _PRELUDE + "fn main() i32:\n" + extra + body + "    return Result.Ok(0)\n"


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


@pytest.mark.parametrize("kind", sorted(_KINDS))
@pytest.mark.parametrize("position", sorted(_POSITIONS))
def test_a_constant_has_no_address_in_every_borrow_position(analyze, position, kind):
    codes = _codes(analyze(_program(position, kind), name="m"))
    if kind in _REFUSED:
        assert "CE2400" in codes, (
            f"a {kind} in the {position} position was not refused with CE2400; "
            f"got {codes}")
    else:
        assert "CE2400" not in codes, (
            f"a {kind} in the {position} position has an address and must be "
            f"accepted; got {codes}")


@pytest.mark.parametrize("position", sorted(_POSITIONS))
def test_one_fault_gets_one_diagnostic(analyze, position):
    """A gate asked twice by two walks over one argument told the user twice."""
    codes = _codes(analyze(_program(position, "constant"), name="m"))
    assert codes.count("CE2400") == 1, (
        f"the {position} position reported CE2400 {codes.count('CE2400')} times; "
        f"got {codes}")


def test_an_index_borrow_is_not_a_ce2400_position(analyze):
    """Measured, and it is not the gate's: `poke arr[0]` has no stable address at all.

    The refusal is CE2404 for every kind, a local array included, so the element
    position never reaches the question this gate answers.
    """
    src = (_PRELUDE + "const i32[3] TABLE = [1, 2, 3]\n\nfn main() i32:\n"
           "    takes(poke TABLE[0])\n    return Result.Ok(0)\n")
    codes = _codes(analyze(src, name="m"))
    assert "CE2404" in codes and "CE2400" not in codes, codes


# --- The seam: one emit site for the code, and every caller reads the scoped lookup.

def _emitters_of(code: str) -> list[str]:
    """Every source line that hands `code` to an emitter, with its file and line."""
    hits: list[str] = []
    pattern = re.compile(rf"\ber\.(?:ERR\.)?{code}\b")
    for path in SOURCE_ROOT.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or not pattern.search(line):
                continue
            hits.append(f"{path.relative_to(SOURCE_ROOT)}:{number}")
    return hits


def test_the_code_is_emitted_from_one_place_only():
    """A sixth emit site outside the seam is what #685 closed; keep it closed."""
    hits = [hit for hit in _emitters_of("CE2400")
            if not hit.startswith("semantics/constant_borrow.py")]
    assert not hits, (
        f"CE2400 is emitted outside the seam at: {hits}.\n"
        "Call `reject_borrow_of_constant` instead. Five sites with four predicates is "
        "what #685 collapsed, and one of them read the FLAT constant table."
    )


def test_the_seam_exists_and_holds_the_gate():
    assert SEAM.is_file(), f"the seam module is missing: {SEAM}"
    tree = ast.parse(SEAM.read_text(encoding="utf-8"))
    names = {node.name for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef)}
    assert "reject_borrow_of_constant" in names, sorted(names)


def test_no_caller_reads_the_flat_constant_table():
    """`by_name` is program-wide and first-wins; the question is per unit."""
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "const_table.by_name.get(" in line and not line.strip().startswith("#"):
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{number}")
    assert not offenders, (
        f"a flat constant lookup stands at: {offenders}.\n"
        "Use the scoped `const_sig(name)` / `const_table.lookup(name, unit, scope)`: "
        "the flat view is first-wins across the whole program, so it answers with "
        "another unit's declaration of the same name (#685)."
    )
