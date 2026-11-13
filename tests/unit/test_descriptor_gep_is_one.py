"""Only `backend/gep_utils.py` reaches the length and the capacity of a `T[]` descriptor
(#878, #1302).

A `T[]` descriptor and a `List@(T)` are `{i64 len, i64 cap, T* data}`. The length and the
capacity have ONE spelling: the value helpers `load_len`, `load_cap`, `store_len`,
`store_cap`, `extract_len`, `extract_cap`, `insert_len` and `insert_cap` of `gep_utils`.
They hold the width of the stored field (i64) and of the surface value (i32,
`backend/stored_width.py`), so no caller loads or stores a count of the wrong width.
`gep_dynamic_array_data` is the one spelling of the data address.

This module refuses each retired name, and, outside `gep_utils.py`, a raw access to field 0
or field 1:

- an `extract_value` / `insert_value` of field 0 or 1 whose aggregate is named as a
  descriptor (`arr`, `array`, `list`, `lst`, `desc`, `header`, `vec`, `bucket`), or whose
  result or `name=` is a count (`len`, `cap`, `count`);
- a GEP `[0, 0]` or `[0, 1]` with the same names.

The scan reads names, so it cannot see a site that names nothing; `KNOWN_OTHER` lists the
sites that read field 0 or 1 of a struct that is NOT a descriptor. `test_run_generator_layouts.py`
checks the `run()` generator directly.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
SEAM_MODULE = "backend/gep_utils.py"

_DESCRIPTOR_NAME = re.compile(r"arr|list|lst|desc|header|vec|bucket", re.I)
_COUNT_NAME = re.compile(r"len|cap|count", re.I)
_COUNT_FIELDS = {0: "len", 1: "cap"}

# (path under sushi_lang/, the `name=` or the target) of a field-0 or field-1 access of a
# struct that is not a descriptor: the string builder buffer `{i8* data, i32 len, i32 cap}`.
KNOWN_OTHER = {
    ("backend/runtime/string_builder.py", "length"),
    ("backend/runtime/string_builder.py", "len_slot"),
}

RETIRED = {
    "get_dynamic_array_len_ptr", "get_dynamic_array_cap_ptr", "get_dynamic_array_data_ptr",
    "gep_dynamic_array_len", "gep_dynamic_array_cap",
    "get_list_len_ptr", "get_list_capacity_ptr", "LIST_LEN_INDICES", "LIST_CAP_INDICES",
}


def _root_name(node: ast.AST) -> str:
    """The last name of an expression: `func.args[0]` gives `args`, `self.arr` gives `arr`."""
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript):
            node = node.value
            continue
        return ""


def _literal_field(node: ast.AST) -> int | None:
    """0 or 1 for a literal `0` / `1`; else None."""
    if isinstance(node, ast.Constant) and type(node.value) is int and node.value in _COUNT_FIELDS:
        return node.value
    return None


def _gep_field(node: ast.AST) -> int | None:
    """0 or 1 for `0`, `1`, `ir.Constant(i32, 0|1)`, `zero` or `one`; else None."""
    if _literal_field(node) is not None:
        return _literal_field(node)
    if isinstance(node, ast.Name):
        return {"zero": 0, "zero_i32": 0, "one": 1, "one_i32": 1}.get(node.id.lower())
    if (isinstance(node, ast.Call) and len(node.args) == 2
            and isinstance(node.func, ast.Attribute) and node.func.attr == "Constant"):
        return _literal_field(node.args[1])
    return None


def _keyword(call: ast.Call, name: str) -> str:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return ""


def raw_count_sites(source: str) -> list[tuple[int, str]]:
    """`(line, label)` of each raw access to the length or the capacity field."""
    targets: dict[int, str] = {}
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            targets[node.value.lineno] = node.targets[0].id
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        field = None
        if attr == "extract_value" and len(node.args) >= 2:
            field = _literal_field(node.args[1])
        elif attr == "insert_value" and len(node.args) >= 3:
            field = _literal_field(node.args[2])
        elif attr == "gep" and len(node.args) >= 2 and isinstance(node.args[1], ast.List) \
                and len(node.args[1].elts) == 2:
            field = _gep_field(node.args[1].elts[1])
        if field is None:
            continue
        label = _keyword(node, "name") or targets.get(node.lineno, "")
        if _DESCRIPTOR_NAME.search(_root_name(node.args[0])) or _COUNT_NAME.search(label):
            found.append((node.lineno, label or ast.unparse(node.args[0])))
    return sorted(found)


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


def test_no_raw_count_access_outside_the_seam():
    found = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        rel = path.relative_to(SOURCE_ROOT).as_posix()
        if rel == SEAM_MODULE:
            continue
        for line, label in raw_count_sites(path.read_text(encoding="utf-8")):
            if (rel, label) not in KNOWN_OTHER:
                found.append(f"{rel}:{line}: {label}")
    assert not found, (
        "read and write the length and the capacity of a T[] or a List@(T) through "
        "backend/gep_utils.py (load_len, load_cap, store_len, store_cap, extract_len, "
        "extract_cap, insert_len, insert_cap); the field is an i64 and the surface is an "
        "i32:\n" + "\n".join(found))


def test_the_known_other_sites_exist():
    for rel, label in KNOWN_OTHER:
        labels = {site[1] for site in raw_count_sites((SOURCE_ROOT / rel).read_text())}
        assert label in labels, f"{rel}: {label} is no longer a field-0/1 site; remove the row"


def test_the_gate_sees_a_raw_site():
    source = '''
def emit(builder, arr_value, list_ptr, header, fat, one):
    n = builder.extract_value(arr_value, 0)
    builder.insert_value(header, n, 1)
    builder.gep(list_ptr, [zero, ir.Constant(i32, 1)])
    cap = builder.extract_value(fat, 1)
    builder.gep(self_ptr, [zero, one], name="vec_len")
    data = builder.extract_value(arr_value, 2)
    env = builder.extract_value(closure, 1, name="closure_env")
    slot = builder.gep(entries, [index])
'''
    assert [line for line, _ in raw_count_sites(source)] == [3, 4, 5, 6, 7]
