"""The reader that the two AST import gates share (docs/design/ir.md 6.4).

It reads the source of each module and finds each import that names the AST: the node
module `semantics/ast.py`, the AST walks `semantics/ast_walk.py` and the AST builder
`semantics/ast_builder/`. An import at module level, under `TYPE_CHECKING` and in a
function body all count, and a relative import counts as the module it resolves to.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AST_MODULES = ("sushi_lang.semantics.ast", "sushi_lang.semantics.ast_walk",
               "sushi_lang.semantics.ast_builder")
#: The name of an import that takes a whole AST module: `from sushi_lang.semantics import ast`.
WHOLE_MODULE = "*"


def _names_the_ast(module: str) -> bool:
    return any(module == name or module.startswith(name + ".") for name in AST_MODULES)


def _imports(node: ast.AST, package: list[str]) -> list[tuple[str, str]]:
    """Each (module, name) of the AST that one import statement takes."""
    if isinstance(node, ast.Import):
        return [(alias.name, WHOLE_MODULE) for alias in node.names
                if _names_the_ast(alias.name)]
    if not isinstance(node, ast.ImportFrom):
        return []
    if node.level == 0:
        module = node.module or ""
    else:
        base = package[:len(package) - (node.level - 1)]
        module = ".".join(base + ([node.module] if node.module else []))
    found = []
    for alias in node.names:
        if _names_the_ast(module):
            found.append((module, alias.name))
        elif _names_the_ast(f"{module}.{alias.name}"):
            found.append((f"{module}.{alias.name}", WHOLE_MODULE))
    return found


def ast_imports(path: Path, source: str | None = None) -> dict[str, set[str]]:
    """The AST modules that the module at `path` imports, each with the names it takes.
    `source` is the text of the module, read from `path` when it is None."""
    package = list(path.relative_to(ROOT).parts[:-1])
    if source is None:
        source = path.read_text(encoding="utf-8")
    found: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(source, filename=str(path))):
        for module, name in _imports(node, package):
            found.setdefault(module, set()).add(name)
    return found


def scan(paths: list[Path]) -> dict[str, dict[str, set[str]]]:
    """For each module of `paths` that imports the AST, by its path under the root: the AST
    modules it imports and the names that it takes from each."""
    found = {}
    for path in paths:
        imports = ast_imports(path)
        if imports:
            found[path.relative_to(ROOT).as_posix()] = imports
    return found
