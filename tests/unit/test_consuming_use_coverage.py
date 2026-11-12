"""Nothing may bypass the ownership seam. No allowlist.

The borrow pass on SHIR decides the `Ownership` of each consuming use ONE time, through
`classify()`, and writes the answer as a row of `OwnershipResults` (docs/design/ir.md 7.9,
10.3): one function of the pass writes the row, and the pass checks the table for each
consuming use when it ends a body with no error (`check_ownership`, CE0157). SHIR-to-SLIR
lowering reads the row and makes `Move` or `Copy`.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "sushi_lang"
BORROW = SOURCE / "semantics" / "passes" / "borrow"


def _called(node: ast.Call) -> str | None:
    func = node.func
    return func.id if isinstance(func, ast.Name) else (
        func.attr if isinstance(func, ast.Attribute) else None)


def _functions_that_call(name: str) -> list[str]:
    """Each function under `passes/borrow/` that calls `name`, as `module:function`."""
    found: list[str] = []
    for path in sorted(BORROW.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            if any(isinstance(node, ast.Call) and _called(node) == name
                   for node in ast.walk(fn)):
                found.append(f"{path.name}:{fn.name}")
    return found


def test_one_function_of_the_borrow_pass_writes_the_ownership_of_a_consuming_use():
    """`classify()` answers one time per consuming use, and one function writes the answer:
    a second writer is a second derivation of the rule."""
    writers = _functions_that_call("record_ownership")
    assert len(writers) == 1, (
        f"the Ownership row of a consuming use has {len(writers)} writers in "
        f"passes/borrow/: {writers}. Write it in the one decision of the pass.")


def test_the_one_writer_asks_classify():
    """The one writer of the row writes what `classify()` answers, and nothing else."""
    (writer,) = _functions_that_call("record_ownership")
    module, function = writer.split(":")
    tree = ast.parse((BORROW / module).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == function)
    called = {_called(node) for node in ast.walk(fn) if isinstance(node, ast.Call)}
    assert "classify" in called, f"{writer} writes an Ownership that classify() did not answer"


def test_the_borrow_pass_checks_its_table_when_it_ends_a_body():
    """CE0157 finds a consuming use with no row: the check runs on each finished body."""
    assert _functions_that_call("check_ownership"), (
        "no function of passes/borrow/ calls check_ownership: a consuming use with no "
        "Ownership row would reach SLIR lowering")
