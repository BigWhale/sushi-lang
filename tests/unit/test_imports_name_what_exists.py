"""Every import of a `sushi_lang` module names a module and a name that exist.

An import inside a function runs only when the function runs, so a deleted module behind
it is found by the first program that reaches the function, as an internal error. This
scan reads the source alone and finds it at once: each `import` and `from ... import`
under `sushi_lang/`, at module level, under `TYPE_CHECKING` and inside a function, must
name a module that is a file or a package, and each imported name must be a submodule or
a name that the module binds at its top level.

The AST body emitter is deleted (docs/design/ir.md 8.2, 8.12), and no code may read an
attribute of `LLVMCodegen` that went with it.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "sushi_lang"

#: The attributes of `LLVMCodegen` that went with the AST body emitter.
DELETED_CODEGEN_ATTRIBUTES = frozenset({
    "expressions", "statements", "memory", "moves", "dynamic_arrays", "print_frames",
    "loop_stack", "current_conditional_moves",
})


def _sources():
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_path(dotted: str) -> Path | None:
    """The file or the package directory of a dotted `sushi_lang` name, or None."""
    base = ROOT.joinpath(*dotted.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if base.is_dir() and any(base.rglob("*.py")):
        return base
    return None


def _bound_names(path: Path) -> set[str]:
    """The names a module binds at its top level, its blocks and its imports included."""
    if path.is_dir():
        init = path / "__init__.py"
        names = {child.stem for child in path.iterdir()
                 if child.suffix == ".py" or (child.is_dir() and child.name != "__pycache__")}
        if init.is_file():
            names |= _bound_names(init)
        return names
    names: set[str] = set()
    todo = list(ast.parse(path.read_text(encoding="utf-8")).body)
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            todo.extend(node.body)
            todo.extend(getattr(node, "orelse", []))
            todo.extend(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                todo.extend(handler.body)
    return names


def _absolute(path: Path, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = path.relative_to(ROOT).with_suffix("").parts[:-1]
    package = package[:len(package) - node.level + 1]
    return ".".join(package + ((node.module,) if node.module else ()))


def _owners(tree: ast.Module) -> dict[int, str]:
    """The qualified name of the innermost function or class around each node, by `id`."""
    owner: dict[int, str] = {}

    def walk(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = f"{name}.{child.name}" if name else child.name
            elif isinstance(child, ast.ClassDef):
                inner = f"{name}.{child.name}" if name else child.name
            owner[id(child)] = inner
            walk(child, inner)

    walk(tree, "")
    return owner


def _faults(path: Path, tree: ast.Module) -> list[tuple[str, str]]:
    """Each missing import or deleted `codegen` attribute in `path`, as (function, what)."""
    owner = _owners(tree)
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        where = owner.get(id(node), "")
        if isinstance(node, ast.ImportFrom):
            module = _absolute(path, node)
            if not module.startswith("sushi_lang"):
                continue
            target = _module_path(module)
            if target is None:
                found.append((where, f"module {module}"))
                continue
            bound = _bound_names(target)
            found.extend((where, f"{module}.{alias.name}") for alias in node.names
                         if alias.name != "*" and alias.name not in bound)
        elif isinstance(node, ast.Import):
            found.extend((where, f"module {alias.name}") for alias in node.names
                         if alias.name.startswith("sushi_lang")
                         and _module_path(alias.name) is None)
        elif (isinstance(node, ast.Attribute) and node.attr in DELETED_CODEGEN_ATTRIBUTES
              and ((isinstance(node.value, ast.Name) and node.value.id == "codegen")
                   or (isinstance(node.value, ast.Attribute)
                       and node.value.attr == "codegen"))):
            found.append((where, f"codegen.{node.attr}"))
    return found


def _all_faults() -> dict[tuple[str, str], list[str]]:
    faults: dict[tuple[str, str], list[str]] = {}
    for path, tree in _sources():
        rel = path.relative_to(PACKAGE).as_posix()
        for where, what in _faults(path, tree):
            faults.setdefault((rel, where), []).append(what)
    return faults


def test_every_import_names_what_exists():
    outside = _all_faults()
    assert not outside, (
        "an import names a module or a name that does not exist, or code reads an "
        "attribute that LLVMCodegen no longer has:\n  "
        + "\n  ".join(f"{path}:{where or '<module>'}: {', '.join(what)}"
                      for (path, where), what in sorted(outside.items())))


def test_an_owner_of_a_method_names_its_class(tmp_path):
    source = tmp_path / "probe.py"
    source.write_text("class Box:\n    def run(self):\n"
                      "        from sushi_lang.backend.statements import loops\n"
                      "def run():\n    from sushi_lang.backend.statements import loops\n")
    found = _faults(source, ast.parse(source.read_text()))
    assert sorted(w for w, _what in found) == ["Box.run", "run"]


def test_the_scan_sees_a_missing_import(tmp_path):
    source = tmp_path / "probe.py"
    source.write_text("def f():\n    from sushi_lang.backend.statements import loops\n"
                      "    return codegen.memory\n")
    found = _faults(source, ast.parse(source.read_text()))
    assert sorted(found) == [("f", "codegen.memory"),
                             ("f", "module sushi_lang.backend.statements")]
