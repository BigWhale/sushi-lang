"""Only `backend/gep_utils.py` reaches the fields of a `T[]` descriptor (#878, #1302).

A `T[]` descriptor and a `List@(T)` are `{i64 len, i64 cap, T* data}`. Three spellings
used to compute the address of a descriptor field: the `LLVMTypeSystem.get_dynamic_array_*_ptr`
trio, the `gep_utils.gep_dynamic_array_*` trio, and a raw GEP with a literal 0, 1 or 2.
The `List@(T)` had a fourth (`get_list_len_ptr`, `get_list_capacity_ptr`). Now the length
and the capacity have ONE spelling: the value helpers `load_len`, `load_cap`, `store_len`,
`store_cap`, `extract_len`, `extract_cap`, `insert_len` and `insert_cap` of `gep_utils`.
They hold the width of the stored field (i64) and of the surface value (i32), so no caller
holds the address of a count, and no caller loads or stores a count of the wrong width.
`gep_dynamic_array_data` is the one spelling of the data address.

This module refuses each retired name. The source alone cannot tell a raw descriptor GEP
or a raw `extract_value` from another of the same struct shape, so those are not checked
here; a wrong width there is an LLVM type error at the next build. `test_run_generator_layouts.py`
checks the `run()` generator directly.
"""
from __future__ import annotations

import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

RETIRED = {
    "get_dynamic_array_len_ptr", "get_dynamic_array_cap_ptr", "get_dynamic_array_data_ptr",
    "gep_dynamic_array_len", "gep_dynamic_array_cap",
    "get_list_len_ptr", "get_list_capacity_ptr", "LIST_LEN_INDICES", "LIST_CAP_INDICES",
}


def test_the_retired_spellings_are_gone():
    """No retired descriptor-field helper is defined or named."""
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = (node.attr if isinstance(node, ast.Attribute)
                    else node.id if isinstance(node, ast.Name)
                    else node.name if isinstance(node, ast.FunctionDef)
                    else node.name if isinstance(node, ast.alias) else None)
            if name in RETIRED:
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{getattr(node, 'lineno', '?')} {name}")
    assert not offenders, (
        "a retired descriptor-field helper is back:\n  " + "\n  ".join(offenders)
        + "\nRead and write a length or a capacity with gep_utils.load_len / load_cap / "
        "store_len / store_cap (by address) or extract_len / insert_len (by value).")
