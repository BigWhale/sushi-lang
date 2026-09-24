"""An extension-method symbol has ONE namer, `extension_symbol` (#832).

The declaration of a perk-implementation method named its symbol through
`extension_symbol`, while the perk call site and the manifest's `impl_method_symbol` wrote
the same symbol by hand from `sanitize_extension_receiver`. The hand spelling drops the
`__<method type args>` suffix. The two agree only while a perk method cannot declare a
method-level type parameter. So the receiver sanitizer has no caller outside the module
that owns the symbol.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
HOME = ROOT / "semantics" / "generics" / "name_mangling.py"


def _calls(path: Path, name: str) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == name or getattr(node.func, "attr", None) == name)
    ]


def test_the_receiver_sanitizer_is_called_only_where_the_symbol_is_named():
    hits = [
        f"{path.relative_to(ROOT)}:{lineno}"
        for path in sorted(ROOT.rglob("*.py")) if path != HOME
        for lineno in _calls(path, "sanitize_extension_receiver")
    ]
    assert hits == []


def test_the_control_finds_the_calls_in_the_home_module():
    assert _calls(HOME, "sanitize_extension_receiver")


def test_a_perk_method_symbol_is_the_extension_symbol():
    from sushi_lang.semantics.generics.name_mangling import extension_symbol
    from sushi_lang.semantics.library_templates import impl_method_symbol
    for receiver, method in [("i32", "show"), ("Box<i32>", "unwrap"), ("i32[]", "sum"),
                             ("Pair<i32, string>", "left")]:
        assert impl_method_symbol(receiver, method) == extension_symbol(receiver, method)
