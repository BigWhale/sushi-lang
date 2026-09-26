"""Every borrow position asks ONE gate what this name's record permits (#685, #713).

CE2400 -- the borrow refusal -- was written at five sites with four predicates, and the
fourth read the FLAT constant table. `by_name` is first-wins over the whole program, so a
unit whose own `var` shares a name with another unit's `const` was refused, and a unit
whose own `const` shares a name with another unit's `var` was let through to write
read-only memory. The multi-unit halves of that measurement are fixtures under
`tests/references/borrow_of_constant/`; the matrix below is the single-unit half, plus the
gate that keeps the emission in one place.

The matrix carries the MODE since #713. A constant is read-only STORAGE: `peek` reads
through a pointer into `.rodata`, and only a write -- `poke`, or the `nom` take -- is the
fault. Before the ruling the POSITION decided: three of the four `peek` positions refused
and the match arm allowed it, while a bare pattern binding, which the ownership model also
calls a borrow, was allowed in all of them.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SEAM = SOURCE_ROOT / "semantics" / "constant_borrow.py"


# Each position writes the name `{n}`; `{e}` is an enum-typed name for the match arm.
# The mode is the position's own: a `poke` writes through the pointer, a `peek` reads.

# kind -> (the extra local lines, the i32 name, the Shade name)














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


def test_every_caller_names_the_mode():
    """The mode is what the record is asked about, so no caller may leave it out (#713)."""
    text = SEAM.read_text(encoding="utf-8")
    tree = ast.parse(text)
    gate = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "reject_borrow_of_constant")
    assert [arg.arg for arg in gate.args.kwonlyargs][:1] == ["mode"], ast.dump(gate.args)
    assert gate.args.kw_defaults[0] is None, "the mode has a default, so a caller can skip it"


# --- The take is NOT this gate's: a `nom` is a consuming use (#726).




