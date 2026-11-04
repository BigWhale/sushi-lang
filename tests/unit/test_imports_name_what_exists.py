"""Every import of a `sushi_lang` module names a module and a name that exist.

An import inside a function runs only when the function runs, so a deleted module behind
it is found by the first program that reaches the function, as an internal error. This
scan reads the source alone and finds it at once: each `import` and `from ... import`
under `sushi_lang/`, at module level, under `TYPE_CHECKING` and inside a function, must
name a module that is a file or a package, and each imported name must be a submodule or
a name that the module binds at its top level.

The AST body emitter is deleted (docs/design/ir.md 8.2, 8.12). The emitter families keep
their AST argument half, with no caller, until the group of each one gives it operands
(Q7), and the AST helpers of the borrow pass wait for their group in the same way.
`PORT_TEMPLATES` names each function that holds such a half: there, and only there, an
import may name a deleted module, and a read may name an attribute that `LLVMCodegen` no
longer has. The list may only get SHORTER, and the SLIR emitter names none of them.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "sushi_lang"
SLIR_EMITTER = PACKAGE / "backend" / "slir"

#: The attributes of `LLVMCodegen` that went with the AST body emitter.
DELETED_CODEGEN_ATTRIBUTES = frozenset({
    "expressions", "statements", "memory", "moves", "dynamic_arrays", "print_frames",
    "loop_stack", "current_conditional_moves",
})

_B = "backend/"

#: (path under sushi_lang/, qualified function name) -> the group that ports it.
PORT_TEMPLATES: dict[tuple[str, str], str] = {
    (_B + "expressions/calls/stdlib/process.py", "_emit_run"): "D7",
    (_B + "expressions/calls/stdlib/process.py", "_emit_exit"): "D7",
    (_B + "expressions/calls/stdlib/signatures.py", "emit_registry_call"): "D7",
    (_B + "generics/hashmap/methods/iterators.py", "_emit_bucket_iterator.owned"): "C3",
    (_B + "generics/hashmap/methods/mutations.py", "emit_hashmap_insert"): "B4",
    (_B + "generics/hashmap/probe.py", "emit_lookup_key"): "B4",
    (_B + "generics/list/methods_iter.py", "emit_list_iter.owned"): "C3",
    (_B + "generics/own.py", "emit_builtin_own_method"): "B5",
    (_B + "ranges.py", "emit_range"): "C3",
    (_B + "runtime/closures.py", "emit_lambda"): "C2",
    (_B + "runtime/iterator_adapters.py", "emit_adapter_call"): "C3",
    (_B + "types/arrays/methods/iterators.py", "emit_dynamic_array_iter.owned"): "C3",
    (_B + "types/arrays/methods/iterators.py", "emit_fixed_array_iter.owned"): "C3",
    # The AST helpers of the borrow pass that the switch left with no caller (P2.6a).
    ("semantics/passes/borrow/bindings.py", "bind_let_reference"): "B8",
    ("semantics/passes/borrow/borrowed_returns.py", "check_borrowed_return"): "B8",
}

#: The rows of `PORT_TEMPLATES` at the day of the pin. A row may leave both; none may join.
PINNED_PORT_TEMPLATES: frozenset[tuple[str, str]] = frozenset({
    (_B + "expressions/calls/stdlib/process.py", "_emit_run"),
    (_B + "expressions/calls/stdlib/process.py", "_emit_exit"),
    (_B + "expressions/calls/stdlib/signatures.py", "emit_registry_call"),
    (_B + "generics/hashmap/methods/iterators.py", "_emit_bucket_iterator.owned"),
    (_B + "generics/hashmap/methods/mutations.py", "emit_hashmap_insert"),
    (_B + "generics/hashmap/probe.py", "emit_lookup_key"),
    (_B + "generics/list/methods_iter.py", "emit_list_iter.owned"),
    (_B + "generics/own.py", "emit_builtin_own_method"),
    (_B + "ranges.py", "emit_range"),
    (_B + "runtime/closures.py", "emit_lambda"),
    (_B + "runtime/iterator_adapters.py", "emit_adapter_call"),
    (_B + "types/arrays/methods/iterators.py", "emit_dynamic_array_iter.owned"),
    (_B + "types/arrays/methods/iterators.py", "emit_fixed_array_iter.owned"),
    ("semantics/passes/borrow/bindings.py", "bind_let_reference"),
    ("semantics/passes/borrow/borrowed_returns.py", "check_borrowed_return"),
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
    outside = {key: what for key, what in _all_faults().items() if key not in PORT_TEMPLATES}
    assert not outside, (
        "an import names a module or a name that does not exist, or code reads an "
        "attribute that LLVMCodegen no longer has:\n  "
        + "\n  ".join(f"{path}:{where or '<module>'}: {', '.join(what)}"
                      for (path, where), what in sorted(outside.items())))


def test_the_port_templates_only_shrink():
    faults = _all_faults()
    clean = sorted(key for key in PORT_TEMPLATES if key not in faults)
    assert not clean, f"these port templates name nothing deleted; delete their rows: {clean}"


def test_no_port_template_row_is_new():
    grown = sorted(set(PORT_TEMPLATES) - PINNED_PORT_TEMPLATES)
    assert not grown, f"a port template row may be deleted and never added: {grown}"
    stale = sorted(PINNED_PORT_TEMPLATES - set(PORT_TEMPLATES))
    assert not stale, (
        f"delete the pin row together with the port template row, or the row can return: {stale}")


def _reaches(tree: ast.AST, names: set[str]) -> list[tuple[int, str]]:
    """Each line of `tree` that names one of `names`, as (line, name).

    A name is a bare read, an attribute, an imported name or a string, so a call through a
    table of functions, a module attribute or a lookup by name is seen as well.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.alias):
            name = node.name.split(".")[-1]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            name = node.value
        else:
            continue
        if name in names:
            found.append((getattr(node, "lineno", 0), name))
    return found


def _scanned_name(where: str) -> str:
    """The name of the function or method that a row names. A class prefix and a nested
    function name are not part of it."""
    parts = where.split(".")
    return parts[1] if len(parts) > 1 and parts[0] != parts[0].lower() else parts[0]


def test_the_slir_emitter_names_no_port_template():
    names = {_scanned_name(where) for _path, where in PORT_TEMPLATES}
    hits = [f"{path.relative_to(PACKAGE)}:{line} {name}"
            for path in sorted(SLIR_EMITTER.rglob("*.py"))
            for line, name in _reaches(ast.parse(path.read_text(encoding="utf-8")), names)]
    assert not hits, hits


def test_the_reach_scan_sees_a_table_and_a_module_attribute():
    source = ("from m import emit_a as alias\n"
              "TABLE = {'x': emit_b, 'y': mod.emit_c}\n"
              "ROWS = (emit_d,)\n"
              "NAMED = {'emit_e': 1}\n")
    found = _reaches(ast.parse(source), {"emit_a", "emit_b", "emit_c", "emit_d", "emit_e"})
    assert sorted(name for _line, name in found) == [
        "emit_a", "emit_b", "emit_c", "emit_d", "emit_e"]


def test_a_method_row_is_scanned_by_its_own_name():
    assert _scanned_name("Box.run") == "run"
    assert _scanned_name("Box.run.inner") == "run"
    assert _scanned_name("emit_list_push.element") == "emit_list_push"
    assert _scanned_name("_Steps.run") == "run"


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
