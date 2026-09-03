"""A stdlib constant is a name the ladder rules, not a builtin outside every table (#560).

Three readers -- the scope pass, the inference visitor and the back end's name emitter --
each asked the math module's `is_builtin_math_constant` BEFORE the local and constant
tables, so `let i32 PI = 3` printed 3.14159 and a unit's own `const f64 E` was replaced
by 2.71828, with no `use <math>` in sight. The class of bug is a builtin name that lives
outside the tables. This gate keeps the math module's two hooks where the registry reads
them and nowhere else, and pins the one lookup every reader takes instead.
"""
from __future__ import annotations

import math
from pathlib import Path

from sushi_lang.semantics.namespaces import UnitScope
from sushi_lang.semantics.stdlib_registry import (
    get_stdlib_registry,
    lookup_stdlib_constant,
)

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

# The discovery protocol: the module declares its constants, the registry reads them.
THE_ONE_READER = {
    ROOT / "semantics" / "stdlib_registry.py",
    ROOT / "sushi_stdlib" / "src" / "math" / "__init__.py",
}
HOOKS = ("is_builtin_math_constant", "get_builtin_math_constant_value")


def test_only_the_registry_reads_the_math_hooks() -> None:
    offenders = sorted(
        str(path.relative_to(ROOT.parent))
        for path in ROOT.rglob("*.py")
        if path not in THE_ONE_READER
        and any(hook in path.read_text(encoding="utf-8") for hook in HOOKS)
    )
    assert offenders == [], (
        "a reader names the math constants directly instead of asking the ladder: "
        + ", ".join(offenders)
    )


def test_registry_constant_carries_its_value() -> None:
    constants = get_stdlib_registry().get_module("math").constants
    assert constants["PI"].value == math.pi
    assert constants["E"].value == math.e
    assert constants["TAU"].value == math.tau
    assert all(record.is_constant for record in constants.values())


def test_lookup_honours_the_unit_scope() -> None:
    without = UnitScope(unit="u", everything=False)
    assert lookup_stdlib_constant("PI", without) is None

    with_math = UnitScope(unit="u", modules=("math",), everything=False)
    found = lookup_stdlib_constant("PI", with_math)
    assert found is not None and found.name == "PI" and found.module_path == "math"

    assert lookup_stdlib_constant("TAU", UnitScope.unrestricted()) is not None
    assert lookup_stdlib_constant("TAU", None) is not None
    assert lookup_stdlib_constant("sin", with_math) is None
