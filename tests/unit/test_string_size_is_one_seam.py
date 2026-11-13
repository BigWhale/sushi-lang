"""The size field of the string value has one home: `backend/string_layout.py` (#1303).

The string is `{i8* data, i64 size, i8 owned}`. The surface `size()` and `len()` answer an
i32, so a read truncates and a write extends. `string_size`, `string_size_i64`,
`string_size_at`, `with_string_size`, `string_value` and `string_constant` are the entries.
A raw read or write of field 1 beside them gets the width wrong when the width changes, so
this gate refuses, outside the seam:

- an `extract_value` / `insert_value` of field 1 whose aggregate is named as a string
  (`s`, `str1`, `str_...`, `...string...`, `text...`), or whose result or `name=` is a size or a
  length (`size`, `len`);
- a GEP to field 1 of an address named as a string;
- a second spelling of the type: a three-field `LiteralStructType` of a pointer, an
  integer and an `i8`.

The scan reads names, so it cannot see a site that names nothing; `KNOWN_OTHER` lists the
sites that read field 1 of a struct that is NOT a string.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "sushi_lang"
SEAM_MODULE = "backend/string_layout.py"

_STRING_NAME = re.compile(r"^(s|str\d*|str_\w*|\w*_str|\w*string\w*|text\w*|\w*_text)$", re.I)
_SIZE_NAME = re.compile(r"size|len", re.I)

# (path under sushi_lang/, the `name=` or the target) of a field-1 access of a struct
# that is not a string.
KNOWN_OTHER = {
    ("backend/generics/hashmap/methods/core.py", "hm_size"),
}


def _root_name(node: ast.AST) -> str:
    """The last name of an expression: `func.args[0]` gives `args`, `self.lhs` gives `lhs`."""
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript):
            node = node.value
            continue
        return ""


def _is_one(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == 1


def _keyword(call: ast.Call, name: str) -> str:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return ""


def _gep_index_is_one(node: ast.AST) -> bool:
    """`ir.Constant(i32, 1)`, `one`, `ONE` or `1`."""
    if _is_one(node):
        return True
    if isinstance(node, ast.Name):
        return node.id.lower() in {"one", "one_i32"}
    return (isinstance(node, ast.Call) and len(node.args) == 2 and _is_one(node.args[1])
            and isinstance(node.func, ast.Attribute) and node.func.attr == "Constant")


def _is_string_type_spelling(call: ast.Call) -> bool:
    """`LiteralStructType([<pointer>, <int>, <i8>])`."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "LiteralStructType"
            and call.args and isinstance(call.args[0], ast.List)
            and len(call.args[0].elts) == 3):
        return False
    first, middle, last = (ast.unparse(e) for e in call.args[0].elts)
    pointer = re.search(r"ptr|Pointer|as_pointer", first) is not None
    integer = re.fullmatch(r"(self\.)?i(32|64)|ir\.IntType\((32|64)\)", middle) is not None
    byte = re.fullmatch(r"(self\.)?i8|ir\.IntType\(8\)", last) is not None
    return pointer and integer and byte


def raw_size_sites(source: str) -> list[tuple[int, str]]:
    """`(line, label)` of each raw access to the size field, and of each second spelling of
    the type."""
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
        label = _keyword(node, "name") or targets.get(node.lineno, "")
        if attr == "extract_value" and len(node.args) >= 2 and _is_one(node.args[1]):
            if _STRING_NAME.match(_root_name(node.args[0])) or _SIZE_NAME.search(label):
                found.append((node.lineno, label or ast.unparse(node.args[0])))
        elif attr == "insert_value" and len(node.args) >= 3 and _is_one(node.args[2]):
            if _STRING_NAME.match(_root_name(node.args[0])) or _SIZE_NAME.search(label):
                found.append((node.lineno, label or ast.unparse(node.args[0])))
        elif attr == "gep" and len(node.args) >= 2 and isinstance(node.args[1], ast.List) \
                and len(node.args[1].elts) == 2 and _gep_index_is_one(node.args[1].elts[1]):
            if _STRING_NAME.match(_root_name(node.args[0])):
                found.append((node.lineno, label or ast.unparse(node.args[0])))
        elif _is_string_type_spelling(node):
            found.append((node.lineno, "LiteralStructType"))
    return sorted(found)


def test_no_raw_string_size_access_outside_the_seam():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel == SEAM_MODULE:
            continue
        for line, label in raw_size_sites(path.read_text(encoding="utf-8")):
            if (rel, label) not in KNOWN_OTHER:
                found.append(f"{rel}:{line}: {label}")
    assert not found, (
        "read and write the string size through backend/string_layout.py (string_size, "
        "string_size_i64, string_size_at, with_string_size, string_value, "
        "string_constant); the field is an i64 and the surface is an i32:\n"
        + "\n".join(found))


def test_the_known_other_sites_exist():
    for rel, label in KNOWN_OTHER:
        labels = {site[1] for site in raw_size_sites((PACKAGE / rel).read_text())}
        assert label in labels, f"{rel}: {label} is no longer a field-1 site; remove the row"


def test_the_gate_sees_a_raw_site():
    source = '''
def emit(builder, func, text, fat, i8_ptr, i64):
    size = builder.extract_value(func.args[0], 1)
    n = builder.extract_value(text, 1)
    builder.insert_value(fat, n, 1, name="str_with_size")
    builder.gep(text_ptr, [zero, ir.Constant(i32, 1)])
    ir.LiteralStructType([i8_ptr, i64, ir.IntType(8)])
    env = builder.extract_value(closure, 1, name="closure_env")
    data = builder.extract_value(text, 0)
    cap = builder.insert_value(struct_with_len, n, 1, name="struct_with_cap")
'''
    assert [line for line, _ in raw_size_sites(source)] == [3, 4, 5, 6, 7]
