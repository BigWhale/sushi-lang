"""A local name the compiler makes comes from `hidden_name` alone (#1001).

`NAME` in the grammar is `CNAME`, so a leading `__` is a legal identifier. A hidden local
spelled `__fe_item0` collided with a user variable of that name: a silent wrong value, a
CE0000 and false diagnostics. `sushi_lang/semantics/hidden_names.py` makes a name with a
character `CNAME` refuses. This scan refuses a new `__` name literal in
`sushi_lang/semantics/`, so a new hidden local goes through the helper.

The monomorphizer made two more names by hand (#1015): a pack element `args_0` and a local
of an unrolled `expand` copy `t__x0`. Each put its separator in the middle of an f-string,
so the `__` scan did not see it. A second scan refuses an f-string that spells an
identifier in `sushi_lang/semantics/generics/monomorphize/`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "sushi_lang"
SEMANTICS = PACKAGE / "semantics"

# Program-wide names that are not locals. `_claim_index` in the lift pass steps past a
# name the function table or the struct table already holds, so no collision occurs.
ALLOWED = {
    ("passes/lift.py", "__lambda_"): "a lifted function name, guarded by _claim_index",
    ("passes/lift.py", "__closure_env_"): "an environment struct name, guarded by _claim_index",
}

MONOMORPHIZE = SEMANTICS / "generics" / "monomorphize"

# Identifier-shaped f-strings in the monomorphizer that do not name a local.
ALLOWED_FSTRINGS = {
    ("__init__.py", "f'generic_{kind}s'"): "an attribute of the generic tables, not a local",
    ("transformer.py", "f'{param.name}_{i}'"):
        "#1015: functions.py renames each element with pack_element_name; this name is "
        "never read, and a change to call the helper here is handed back",
}

_IDENTIFIER_PART = re.compile(r"[A-Za-z0-9_]*")

_NAME_LITERAL = re.compile(r"__[A-Za-z0-9_]*")


def _is_name_literal(text: str) -> bool:
    return bool(_NAME_LITERAL.fullmatch(text)) and not text.endswith("__")


def _name_literals(source: str) -> list[tuple[int, str]]:
    """Each string literal and each f-string head that spells a `__` name, with its line."""
    hits: list[tuple[int, str]] = []
    in_fstring: set[int] = set()
    nodes = list(ast.walk(ast.parse(source)))
    for node in nodes:
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                in_fstring.add(id(part))
            head = node.values[0] if node.values else None
            if isinstance(head, ast.Constant) and isinstance(head.value, str) \
                    and head.value.startswith("__") and not head.value.startswith("___"):
                hits.append((node.lineno, head.value))
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in in_fstring and _is_name_literal(node.value):
            hits.append((node.lineno, node.value))
    return hits


def _identifier_fstrings(source: str) -> list[tuple[int, str]]:
    """Each f-string that spells an identifier: a hole, and no literal part but name characters."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.JoinedStr):
            continue
        literals = [part.value for part in node.values if isinstance(part, ast.Constant)]
        has_hole = any(isinstance(part, ast.FormattedValue) for part in node.values)
        if has_hole and any(literals) \
                and all(_IDENTIFIER_PART.fullmatch(str(text)) for text in literals):
            hits.append((node.lineno, ast.unparse(node)))
    return hits


def test_the_fstring_scan_sees_a_synthesized_name():
    source = ('a = f"{param.name}_{i}"\nb = f"{old}__x{copy_index}"\n'
              'c = f"type {name} is bad"\nd = f"{x}"\n')
    assert sorted(text for _, text in _identifier_fstrings(source)) == [
        "f'{old}__x{copy_index}'", "f'{param.name}_{i}'"]


def test_the_allowed_fstrings_are_still_there():
    for (rel, text) in ALLOWED_FSTRINGS:
        found = {t for _, t in _identifier_fstrings((MONOMORPHIZE / rel).read_text("utf-8"))}
        assert text in found, f"{rel}: {text} is gone; remove it from ALLOWED_FSTRINGS"


def test_no_monomorphizer_name_is_an_fstring():
    hits = []
    for path in sorted(MONOMORPHIZE.rglob("*.py")):
        rel = path.relative_to(MONOMORPHIZE).as_posix()
        for line, text in _identifier_fstrings(path.read_text(encoding="utf-8")):
            if (rel, text) not in ALLOWED_FSTRINGS:
                hits.append(f"monomorphize/{rel}:{line}: {text}")
    assert hits == [], "make a hidden local name with hidden_names.py: " + "; ".join(hits)


def test_the_scan_sees_a_name_literal():
    source = 'a = f"__fe_item{n}"\nb = "__closure_env"\nc = "__init__"\n'
    assert sorted(text for _, text in _name_literals(source)) == [
        "__closure_env", "__fe_item"]


def test_the_allowed_names_are_still_there():
    for (rel, literal) in ALLOWED:
        texts = {text for _, text in _name_literals((SEMANTICS / rel).read_text("utf-8"))}
        assert literal in texts, f"{rel}: '{literal}' is gone; remove it from ALLOWED"


def test_no_hidden_local_is_spelled_by_hand():
    hits = []
    for path in sorted(SEMANTICS.rglob("*.py")):
        rel = path.relative_to(SEMANTICS).as_posix()
        for line, text in _name_literals(path.read_text(encoding="utf-8")):
            if (rel, text) not in ALLOWED:
                hits.append(f"semantics/{rel}:{line}: '{text}'")
    assert hits == [], "make a hidden local name with hidden_name(): " + "; ".join(hits)


def test_a_hidden_name_is_not_a_cname():
    from sushi_lang.semantics.hidden_names import hidden_name
    cname = re.compile(r"[_A-Za-z][_A-Za-z0-9]*")
    from sushi_lang.semantics.hidden_names import expand_copy_local_name, pack_element_name
    for name in (hidden_name("fe_item", 0), hidden_name("closure_env"),
                 pack_element_name("args", 0), expand_copy_local_name("t", 0)):
        assert cname.fullmatch(name) is None, name
