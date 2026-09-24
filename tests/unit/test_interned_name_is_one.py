"""An interned generic name is spelled in ONE place, and "is this an X instance" reads one field.

`interned_name(base, args)` in `semantics/generics/interned.py` spells `List<i32>`.
Everything else asks `type_predicates.is_instance_of(ty, "List")`, which reads
`generic_base`.

The scan refuses three spellings in `semantics/` and `backend/`: an f-string that puts `<`
right after a name or a hole (`f"{base}<{args}>"`, `f"Entry<{k}>"`, `f"{name}<"`), a string
constant that IS a prefix (`"List<"`), and a concatenation with `"<"`. A constant is caught
wherever it stands, so a prefix parked in a tuple or passed as an argument is refused too.

`KNOWN` lists the sites that are allowed to stay; it is empty and may only go down.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("sushi_lang/semantics", "sushi_lang/backend")
SEAM = "sushi_lang/semantics/generics/interned.py"

KNOWN: dict[str, int] = {}

_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*<$")
_NAME_THEN_ANGLE = re.compile(r"[A-Za-z0-9_]<$")


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def hand_built_names(source: str) -> list[int]:
    """The line of every hand-built interned name or prefix in one module."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    in_fstring: set[int] = set()
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = node.values
            for i, part in enumerate(parts):
                if not isinstance(part, ast.Constant):
                    continue
                in_fstring.add(id(part))
                text = part.value
                after_hole = i > 0 and isinstance(parts[i - 1], ast.FormattedValue)
                if _NAME_THEN_ANGLE.search(text) or (after_hole and text.startswith("<")):
                    hits.append(node.lineno)
                    break
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and side.value == "<":
                    hits.append(node.lineno)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip and id(node) not in in_fstring
                and _PREFIX.match(node.value)):
            hits.append(node.lineno)
    return sorted(hits)


def _scan() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if rel == SEAM:
                continue
            lines = hand_built_names(path.read_text())
            if lines:
                found[rel] = lines
    return found


def test_the_scan_fires_on_every_spelling():
    assert hand_built_names('n = f"{base}<{args}>"\n') == [1]
    assert hand_built_names('n = f"Entry<{k}, {v}>"\n') == [1]
    assert hand_built_names('n = f"{base}<error>"\n') == [1]
    assert hand_built_names('ok = t.name.startswith(f"{base}<")\n') == [1]
    assert hand_built_names('ok = t.name.startswith("List<")\n') == [1]
    assert hand_built_names('P = ("List<", "Own<")\n') == [1, 1]
    assert hand_built_names('p = base + "<"\n') == [1]


def test_the_scan_passes_what_is_not_a_name():
    assert hand_built_names('m = f"add `use <{module}>` here"\n') == []
    assert hand_built_names('m = f"not found: <{unit}>"\n') == []
    assert hand_built_names('def f():\n    """A `List<T>` docstring."""\n') == []
    assert hand_built_names('m = "a < b"\n') == []


def test_no_module_spells_an_interned_name_by_hand():
    found = _scan()
    new = {path: lines for path, lines in found.items()
           if len(lines) > KNOWN.get(path, 0)}
    assert not new, (
        "an interned name or prefix is spelled by hand; call interned_name "
        f"(semantics/generics/interned.py) or type_predicates.is_instance_of: {new}")


def test_the_known_sites_only_go_down():
    found = _scan()
    stale = {path: count for path, count in KNOWN.items()
             if len(found.get(path, [])) < count}
    assert not stale, f"a known site is gone; lower KNOWN to match: {stale}"


def test_the_seam_spells_the_name_the_tables_hold():
    from sushi_lang.semantics.generics.interned import interned_name
    from sushi_lang.semantics.typesys import BuiltinType

    assert interned_name("List", (BuiltinType.I32,)) == "List<i32>"
    assert interned_name("HashMap", (BuiltinType.STRING, BuiltinType.I32)) == "HashMap<string, i32>"
    assert interned_name("Result", (BuiltinType.I32, "StdError")) == "Result<i32, StdError>"


def test_is_instance_of_reads_the_generic_base():
    from sushi_lang.semantics.type_predicates import generic_base_of, is_instance_of
    from sushi_lang.semantics.typesys import BuiltinType, EnumType, StructType

    listed = StructType("List<i32>", (), generic_base="List", generic_args=(BuiltinType.I32,))
    assert is_instance_of(listed, "List")
    assert is_instance_of(listed, "Own", "List")
    assert not is_instance_of(listed, "Own")
    assert generic_base_of(listed) == "List"

    assert generic_base_of(StructType("List<i32>", ())) is None, "a bare name is never read"

    plain = StructType("Listing", ())
    assert not is_instance_of(plain, "List")
    assert generic_base_of(plain) is None
    assert not is_instance_of(EnumType("Color", ()), "Color")
    assert not is_instance_of(BuiltinType.I32, "List")
    assert not is_instance_of(None, "List")


def test_a_type_parameter_answers_is_pack():
    from sushi_lang.semantics.generics.types import TypeParameter

    assert TypeParameter("T").is_pack is False
