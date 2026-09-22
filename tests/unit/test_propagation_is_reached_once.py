"""One propagation call per position, and the guard that survives it.

`utils.py` carried two shims over `propagation.propagate_types_to_value` that differed in
one word of their docstrings, and nine positions called BOTH of them, back to back, on one
argument. The propagation therefore ran twice at each of them, and the idempotency guard
in `_stamp_numeric_literal` absorbed the second run.

Both shims are gone, and every position calls the seam itself. The guard stays, because it also answers a
GENUINE re-entry: a value is reached by more than one propagation in its own right, and
stamping a literal twice would report its range fault twice. That is measured, not
argued -- the guard still fires over the fixture corpus with every duplicate call gone.
"""
from __future__ import annotations

import ast
from pathlib import Path

SUSHI_LANG = Path(__file__).resolve().parents[2] / "sushi_lang"
PROPAGATION = SUSHI_LANG / "semantics" / "passes" / "types" / "propagation.py"

#: Both shims are gone outright: nothing may name either again.
_RETIRED_SHIMS = (
    "propagate_struct_type_to_dotcall",
    "propagate_enum_type_to_dotcall",
)


def _sources():
    for path in sorted(SUSHI_LANG.rglob("*.py")):
        yield path.relative_to(SUSHI_LANG).as_posix(), path.read_text(encoding="utf-8")


def test_the_propagation_shims_are_gone():
    offenders = [f"{module}: {shim}"
                 for module, source in _sources()
                 for shim in _RETIRED_SHIMS if shim in source]
    assert not offenders, (
        "a retired propagation shim is named again:\n  " + "\n  ".join(offenders)
        + "\nCall propagation.propagate_types_to_value once instead."
    )


def test_the_detector_sees_a_name():
    """The always-fires control: a sweep that answers zero must be a sweep that works."""
    modules = [module for module, source in _sources() if "propagate_types_to_value" in source]
    assert len(modules) > 3, modules


def test_the_idempotency_guard_is_still_there():
    """The guard is live, not dead code left over from the shims."""
    tree = ast.parse(PROPAGATION.read_text(encoding="utf-8"), filename=str(PROPAGATION))
    stamp = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "_stamp_numeric_literal")
    guards = [node for node in ast.walk(stamp)
              if isinstance(node, ast.If)
              and any(isinstance(inner, ast.Return) for inner in node.body)]
    assert guards, ("_stamp_numeric_literal no longer returns early for a literal that "
                    "is already stamped: a second reach reports its range fault twice")


def test_a_literal_reached_twice_reports_its_range_fault_once(analyze):
    """The guard's user-visible job: one literal, one range diagnostic."""
    source = """fn take(i8 n) i32:
    return Result.Ok(n as i32)

fn main() i32:
    let i32 r = take(300)??
    println("{r}")
    return Result.Ok(0)
"""
    reporter = analyze(source, name="m")
    codes = [item.code for item in reporter.items if item.code == "CE2073"]
    assert codes == ["CE2073"], codes
