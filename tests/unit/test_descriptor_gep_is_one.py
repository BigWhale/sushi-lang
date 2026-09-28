"""Only `backend/gep_utils.py` computes the address of a `T[]` descriptor field (#878).

A `T[]` descriptor is `{i32 len, i32 cap, T* data}`. Three spellings used to compute the
address of one of its fields: the `LLVMTypeSystem.get_dynamic_array_*_ptr` trio, the
`gep_utils.gep_dynamic_array_*` trio, and a raw GEP with a literal 0, 1 or 2. Now the
`gep_utils` trio is the one spelling.

This module refuses the retired trio by name. The trio sat on `LLVMTypeSystem`, where a
new helper for a descriptor field goes first, so a return of the names is a real risk.
The source alone cannot tell a raw descriptor GEP from another struct GEP of the same
LLVM type `{i32, i32, T*}` (a `List@(T)`, an iterator slot, the HashMap buckets), so a
raw GEP is not checked here. `test_run_generator_layouts.py` checks the `run()`
generator directly.
"""
from __future__ import annotations

import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

RETIRED = {"get_dynamic_array_len_ptr", "get_dynamic_array_cap_ptr", "get_dynamic_array_data_ptr"}


def test_the_retired_trio_is_gone():
    """`LLVMTypeSystem.get_dynamic_array_*_ptr` is not defined and not called."""
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = (node.attr if isinstance(node, ast.Attribute)
                    else node.id if isinstance(node, ast.Name)
                    else node.name if isinstance(node, ast.FunctionDef) else None)
            if name in RETIRED:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{node.lineno} {name}")
    assert not offenders, (
        "the retired descriptor-field helpers are back:\n  " + "\n  ".join(offenders)
        + "\nUse gep_utils.gep_dynamic_array_len / _cap / _data."
    )
