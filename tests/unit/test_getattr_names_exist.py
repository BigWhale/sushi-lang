"""A `getattr` with a default must name an attribute that exists. No silent misses.

`getattr(block, "stmts", ())` read a field the AST does not have -- `Block.statements`
is the name, and `stmts` is nowhere in the repository. The default answered `()` for
every call, so the body half of the `ptr` quarantine (CE5009) enforced nothing and no
test could see it (#596). A misspelled attribute behind a default is not a crash; it is
a rule that quietly stops applying.

The rule: every attribute name spelled as a string literal in a `getattr`, `hasattr` or
`setattr` inside `sushi_lang/` must exist somewhere in `sushi_lang/` as a real attribute,
or be a flag this project's own CLIs declare, or be named below as owned by a library.
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2] / "sushi_lang"


# Names that belong to something outside `sushi_lang/`, so no definition of them can be
# found here. Each entry says who owns it; nothing else may be added without one.
FOREIGN_ATTRIBUTES = {
    "__dataclass_fields__": "the dataclasses runtime",
    "__dataclass_params__": "the dataclasses runtime",
    "frozen": "dataclasses `__dataclass_params__.frozen`",
    "__version__": "llvmlite",
    "llvm_version_info": "llvmlite",
    "token": "a Lark parse exception",
    "expected": "a Lark parse exception",
    "char": "a Lark parse exception",
    "meta": "a Lark tree node",
}


def _python_files() -> list[pathlib.Path]:
    return sorted(ROOT.rglob("*.py"))


def _trees() -> list[tuple[pathlib.Path, ast.Module]]:
    trees = []
    for path in _python_files():
        try:
            trees.append((path, ast.parse(path.read_text())))
        except SyntaxError:
            continue
    return trees


def _defined_attributes(trees) -> set[str]:
    """Every name this project spells as a real attribute, definition or annotation."""
    defined: set[str] = set()
    for _path, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                defined.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)
        for node in tree.body:                       # a module-level constant
            if isinstance(node, ast.Assign):
                defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return defined


def _argparse_destinations(trees) -> set[str]:
    """Every flag this project's own CLIs declare: argparse writes these onto a Namespace."""
    dests: set[str] = set()
    for _path, tree in trees:
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "add_argument"):
                continue
            for keyword in node.keywords:
                if keyword.arg == "dest" and isinstance(keyword.value, ast.Constant):
                    dests.add(keyword.value.value)
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    dests.add(arg.value.lstrip("-").replace("-", "_"))
    return dests


def _lookups(trees) -> dict[str, list[str]]:
    """Every literal attribute name a dynamic lookup asks for, and where."""
    found: dict[str, list[str]] = {}
    for path, tree in trees:
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("getattr", "hasattr", "setattr")):
                continue
            if len(node.args) < 2:
                continue
            name = node.args[1]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                found.setdefault(name.value, []).append(
                    f"{path.relative_to(ROOT.parent)}:{node.lineno}")
    return found


def test_every_dynamic_lookup_names_a_real_attribute():
    trees = _trees()
    known = _defined_attributes(trees) | _argparse_destinations(trees) | set(FOREIGN_ATTRIBUTES)

    unknown = {name: sites for name, sites in _lookups(trees).items() if name not in known}
    assert not unknown, (
        "a getattr/hasattr/setattr names an attribute that exists nowhere:\n"
        + "\n".join(f"  {name!r}: {sites}" for name, sites in sorted(unknown.items()))
        + "\nA default over a misspelled name answers silently and the rule around it "
          "stops applying. Fix the spelling, or add the owner to FOREIGN_ATTRIBUTES."
    )


def test_the_foreign_list_holds_nothing_this_project_defines():
    """A name that grew a definition here belongs in the tree, not on the list."""
    trees = _trees()
    defined = _defined_attributes(trees)
    stale = sorted(name for name in FOREIGN_ATTRIBUTES if name in defined)
    assert not stale, (
        f"FOREIGN_ATTRIBUTES names something sushi_lang now defines: {stale}"
    )
