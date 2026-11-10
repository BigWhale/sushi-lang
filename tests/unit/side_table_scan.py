"""The scan of the one-writer gates of the side tables of a SHIR body (docs/design/ir.md 7.3, 7.9).

A side table holds the answers of one pass for one body: `TypeckResults`, `OwnershipResults`
and `LiftResults`. The one-writer gate holds the same four rules for each table, over the
Python source of `sushi_lang/`, with the names of its table:

- only the owner of the bodies (`semantics/shir/lowering.py`) makes a table: every other
  module names the class only as a type (an annotation, a `TypeAlias`, a plain import), so
  an alias, an attribute or a string cannot make a table;
- only the modules of the writer pass name the write API: every attribute and every string
  that spells a write method counts, so a method reference, an alias, `getattr`, `map` and
  `functools.partial` cannot write;
- only the data module names the tables that the class holds;
- the class has a read API and a write API, and nothing else.

The samples give each form of a write and of a construction as a small text, so that each
gate shows that its scan finds each form. No scan sees the type of a value, so each table
refuses `copy.copy` and `copy.deepcopy` itself. A name that the code builds at run time
(`getattr(t, "record_" + kind)`) is out of reach of a scan. The scan reads source; it does
not start the compiler.
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

OWNER_MODULE = "semantics/shir/lowering.py"

#: A module outside every writer and every data module.
ELSEWHERE = "semantics/elsewhere.py"

WRITE_FORMS = ("direct", "alias", "getattr", "map", "partial")
CONSTRUCTION_FORMS = ("direct", "alias", "import alias", "attribute", "getattr")


@dataclass(frozen=True)
class SideTable:
    """The names that a gate holds for one table.

    `writer` is the module of the writer pass, or its package with a final `/`.
    """
    name: str
    data_module: str
    writer: str
    read_api: frozenset[str]
    write_api: frozenset[str]
    tables: frozenset[str]

    def writes_here(self, module: str) -> bool:
        return module == self.data_module or module.startswith(self.writer)


def _modules() -> dict[str, ast.AST]:
    return {path.relative_to(SOURCE_ROOT).as_posix(): ast.parse(path.read_text("utf-8"))
            for path in sorted(SOURCE_ROOT.rglob("*.py"))}


MODULES = _modules()


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def calls_of(names: frozenset[str], modules: dict[str, ast.AST] = MODULES) -> dict[str, list[str]]:
    """For each module, the calls of a name in `names`, as `name:line`."""
    found: dict[str, list[str]] = {}
    for module, tree in modules.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called_name(node) in names:
                found.setdefault(module, []).append(f"{_called_name(node)}:{node.lineno}")
    return found


def mentions_of(names: frozenset[str], module: str) -> list[str]:
    """Every name or attribute in `names` that `module` holds, as `name:line`."""
    found = []
    for node in ast.walk(MODULES[module]):
        word = (node.id if isinstance(node, ast.Name)
                else node.attr if isinstance(node, ast.Attribute) else None)
        if word in names:
            found.append(f"{word}:{node.lineno}")
    return found


def stray_table_mentions(table: SideTable) -> dict[str, list[str]]:
    """Each module but the data module that names a table of the class."""
    return {module: mentions_of(table.tables, module) for module in MODULES
            if module != table.data_module and mentions_of(table.tables, module)}


def _annotation_nodes(tree: ast.AST) -> set[int]:
    """The ids of the nodes in a type position: a parameter or return annotation, the
    annotation of a variable, and the value of a `TypeAlias`."""
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            roots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
            if node.value is not None and ast.unparse(node.annotation).endswith("TypeAlias"):
                roots.append(node.value)
    return {id(inner) for root in roots for inner in ast.walk(root)}


def _words(node: ast.AST) -> list[str]:
    """The names that `node` spells: a name, an attribute, a string, an imported name."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.alias):
        return [node.name] + ([node.asname] if node.asname else [])
    return []


def stray_constructions(table: SideTable, modules: dict[str, ast.AST]) -> dict[str, list[str]]:
    """Each mention of the class outside its module and its owner that is not a type.

    A plain import of the name is a type use too, so a reader can annotate with it. An
    import under another name is an alias, and the alias can make a table.
    """
    found: dict[str, list[str]] = {}
    for module, tree in modules.items():
        if module in (table.data_module, OWNER_MODULE):
            continue
        types = _annotation_nodes(tree)
        for node in ast.walk(tree):
            if id(node) in types or table.name not in _words(node):
                continue
            if isinstance(node, ast.alias) and node.asname is None:
                continue
            found.setdefault(module, []).append(f"{table.name}:{getattr(node, 'lineno', 0)}")
    return found


def stray_writes(table: SideTable, modules: dict[str, ast.AST]) -> dict[str, list[str]]:
    """Each mention of a write-API name outside the writer pass and the data module: an
    attribute (a call, an alias, a method reference) or a string (a `getattr`)."""
    found: dict[str, list[str]] = {}
    for module, tree in modules.items():
        if table.writes_here(module):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Attribute, ast.Constant)):
                found.setdefault(module, []).extend(
                    f"{word}:{node.lineno}" for word in _words(node) if word in table.write_api)
    return {module: sites for module, sites in found.items() if sites}


def api_of(cls: type) -> set[str]:
    """The methods of `cls`, with no dunder method."""
    return {name for name, member in inspect.getmembers(cls, inspect.isfunction)
            if not name.startswith("__")}


# -- the samples: each form of a write and of a construction -------------------------


def _a_write(table: SideTable) -> str:
    return min(table.write_api)


def write_samples(table: SideTable) -> dict[str, ast.AST]:
    """Each form of a write, in a module outside the writer pass."""
    write = _a_write(table)
    texts = {
        "direct": f"t.{write}(n, ty)\n",
        "alias": f"w = t.{write}\nw(n, ty)\n",
        "getattr": f"getattr(t, '{write}')(n, ty)\n",
        "map": f"list(map(t.{write}, keys, values))\n",
        "partial": f"functools.partial(t.{write}, n)(ty)\n",
    }
    return {form: ast.parse(text) for form, text in texts.items()}


def construction_samples(table: SideTable) -> dict[str, ast.AST]:
    """Each form of a construction, in a module outside the owner."""
    name, module = table.name, table.data_module.removesuffix(".py").replace("/", ".")
    texts = {
        "direct": f"table = {name}()\n",
        "alias": f"Made = {name}\ntable = Made()\n",
        "import alias": f"from sushi_lang.{module} import {name} as Made\n",
        "attribute": f"table = shir.{name}()\n",
        "getattr": f"table = getattr(shir, '{name}')()\n",
    }
    return {form: ast.parse(text) for form, text in texts.items()}


def clean_samples(table: SideTable) -> dict[str, ast.AST]:
    """Text that the scan must let pass: another name with the same start, a write in the
    writer pass, and a reader that names the class in a type alone."""
    write, name = _a_write(table), table.name
    module = table.data_module.removesuffix(".py").replace("/", ".")
    writer = table.writer + "sample.py" if table.writer.endswith("/") else table.writer
    texts = {
        ELSEWHERE: (f"{write}_site(node)\nself.{write}_site = None\n"
                    f"from sushi_lang.{module} import {name}\n"
                    f"def read(t: {name}, n: Optional[{name}]) -> {name}:\n"
                    "    return t\n"
                    f"Pair: TypeAlias = tuple[int, Optional[{name}]]\n"
                    f"slot: {name}\n"),
        writer: f"w = t.{write}\nw(n, ty)\n",
    }
    return {path: ast.parse(text) for path, text in texts.items()}


# -- the rules, as one assertion each: a gate calls them with its names ---------------


def assert_the_owner_alone_makes(table: SideTable) -> None:
    stray = stray_constructions(table, MODULES)
    assert not stray, (
        f"a module outside {OWNER_MODULE} makes a {table.name}: {stray}. Each body has ONE "
        "table, which lowering lists beside it; read it from the LoweredProgram.")
    assert OWNER_MODULE in calls_of(frozenset({table.name})), (
        f"{OWNER_MODULE} makes no {table.name}; the scan has gone stale")


def assert_the_writer_alone_writes(table: SideTable) -> None:
    stray = stray_writes(table, MODULES)
    assert not stray, (
        f"a module outside {table.writer} writes a row of {table.name}: {stray}. A side "
        "table has one writer (ground rule 9).")


def assert_the_scan_finds_a_write(table: SideTable, form: str) -> None:
    assert stray_writes(table, {ELSEWHERE: write_samples(table)[form]}), form


def assert_the_scan_finds_a_construction(table: SideTable, form: str) -> None:
    assert stray_constructions(table, {ELSEWHERE: construction_samples(table)[form]}), form


def assert_the_scan_lets_a_reader_and_the_writer_pass(table: SideTable) -> None:
    modules = clean_samples(table)
    assert stray_writes(table, modules) == {}
    assert stray_constructions(table, modules) == {}


def assert_the_data_module_alone_names_the_tables(table: SideTable) -> None:
    stray = stray_table_mentions(table)
    assert not stray, f"a module reads or writes the tables of a {table.name} past its API: {stray}"
    assert mentions_of(table.tables, table.data_module), (
        "the tables are gone; the scan has gone stale")


def assert_the_api(table: SideTable, cls: type) -> None:
    methods = api_of(cls)
    assert methods == table.read_api | table.write_api, (
        f"{table.name} has the methods {sorted(methods)}; the gate knows "
        f"{sorted(table.read_api | table.write_api)}. A method that writes goes in the "
        "write API, so that the scan holds it to the writer pass.")


def assert_a_copy_is_refused(cls: type, copier: Callable[[object], object]) -> None:
    """A copy is a second table of one body. No scan can see the type of a copied value,
    so the table refuses the copy itself."""
    from sushi_lang.internals.diagnostics import InternalCompilerError

    with pytest.raises(InternalCompilerError) as caught:
        copier(cls())
    assert caught.value.code == "CE0000"
