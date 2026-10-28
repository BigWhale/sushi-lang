"""No chain of module-level imports leads from `sushi_lang/semantics/` to llvmlite.

`semantics` must never learn what an LLVM type is. Ruff TID251 refuses a DIRECT llvmlite
import there, but a module of `semantics` that imports a stdlib generator module at its
top level loads llvmlite all the same. This scan reads the source alone. It follows each
import that runs when a module loads: the top level of the module and of each class body,
and every block of it except `if TYPE_CHECKING:`. An import inside a function is not an
edge, because it runs only when the function runs.
"""
from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "sushi_lang"
SEMANTICS = "sushi_lang.semantics"
LLVMLITE = "llvmlite"


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _is_type_checking(test: ast.expr) -> bool:
    """The test is `TYPE_CHECKING` or `typing.TYPE_CHECKING`, and nothing else."""
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _load_time_imports(body: list[ast.stmt]):
    """Each import statement that runs when a module with this body loads."""
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        elif isinstance(node, ast.If) and _is_type_checking(node.test):
            yield from _load_time_imports(node.orelse)
        else:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    yield from _load_time_imports([child])
                elif isinstance(child, (ast.excepthandler, ast.match_case)):
                    yield from _load_time_imports(child.body)


def _with_packages(module: str) -> list[str]:
    """The module and each package above it: an import loads every `__init__.py`."""
    parts = module.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


def import_targets(module: str, is_package: bool, source: str, modules: set[str]) -> set[str]:
    """The modules that `module` loads at its top level, `llvmlite` for any of its own."""
    package = module if is_package else module.rpartition(".")[0]
    found: set[str] = set()
    for node in _load_time_imports(ast.parse(source).body):
        if isinstance(node, ast.Import):
            named = [alias.name for alias in node.names]
        else:
            base = node.module or ""
            if node.level:
                anchor = package.split(".")[:len(package.split(".")) - node.level + 1]
                base = ".".join(anchor + ([node.module] if node.module else []))
            named = [base] + [f"{base}.{alias.name}" for alias in node.names
                              if f"{base}.{alias.name}" in modules]
        for name in named:
            if name == LLVMLITE or name.startswith(LLVMLITE + "."):
                found.add(LLVMLITE)
            found |= {m for m in _with_packages(name) if m in modules}
    return found


def import_graph() -> dict[str, set[str]]:
    paths = {_module_name(p): p for p in PACKAGE.rglob("*.py")}
    modules = set(paths)
    return {m: import_targets(m, p.name == "__init__.py", p.read_text(encoding="utf-8"), modules)
            for m, p in paths.items()}


def chains_to_llvmlite(graph: dict[str, set[str]], prefix: str) -> list[list[str]]:
    """The shortest chain from each module under `prefix` that reaches llvmlite, one per
    last module under `prefix` on the chain."""
    chains: dict[str, list[str]] = {}
    for start in sorted(m for m in graph if m == prefix or m.startswith(prefix + ".")):
        came_from: dict[str, str | None] = {start: None}
        todo = deque([start])
        while todo and LLVMLITE not in came_from:
            here = todo.popleft()
            for there in sorted(graph.get(here, ())):
                if there not in came_from:
                    came_from[there] = here
                    todo.append(there)
        if LLVMLITE in came_from:
            chain: list[str] = [LLVMLITE]
            while came_from[chain[-1]] is not None:
                chain.append(came_from[chain[-1]])  # type: ignore[arg-type]
            chain.reverse()
            exit_at = max(i for i, m in enumerate(chain) if m.startswith(prefix))
            chains.setdefault(" -> ".join(chain[exit_at:]), chain)
    return list(chains.values())


def test_semantics_never_reaches_llvmlite():
    chains = chains_to_llvmlite(import_graph(), SEMANTICS)
    assert not chains, (
        "a module-level import chain leads from sushi_lang/semantics/ to llvmlite. Move "
        "what semantics reads to a module that does not import llvmlite:\n  "
        + "\n  ".join(" -> ".join(chain) for chain in chains))


def test_the_scan_follows_every_load_time_import():
    modules = {"p", "p.s", "p.s.a", "p.g", "p.g.common", "p.h"}
    sources = {
        "from p.g.common import factory": {"p", "p.g", "p.g.common"},
        "from ..g import common": {"p", "p.g", "p.g.common"},
        "import llvmlite.ir as ir": {LLVMLITE},
        "try:\n    from p import h\nexcept ImportError:\n    pass": {"p", "p.h"},
        "class C:\n    from p import h": {"p", "p.h"},
        "if TYPE_CHECKING:\n    from p import h": set(),
        "if typing.TYPE_CHECKING:\n    from p import h": set(),
        "if TYPE_CHECKING:\n    pass\nelse:\n    from p import h": {"p", "p.h"},
        "if not TYPE_CHECKING:\n    from p import h": {"p", "p.h"},
        "def f():\n    from p import h": set(),
    }
    for source, expected in sources.items():
        assert import_targets("p.s.a", False, source, modules) == expected, source
    graph = {"p.s.a": {"p.g.common"}, "p.g.common": {LLVMLITE}}
    assert chains_to_llvmlite(graph, "p.s") == [["p.s.a", "p.g.common", LLVMLITE]]
