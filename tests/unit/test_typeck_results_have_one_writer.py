"""Gate: the `typecheck` pass is the one writer of `TypeckResults` (docs/design/ir.md 7.3, ruling S5).

`TypeckResults` is the table of one body: every answer that `typecheck` gives, keyed by a
node, a local or a position. Lowering lists each body with its table, the pass writes the
rows, and `lift`, `borrow`, SLIR lowering and the dump read them. A table with a second
writer is the stamp channel again (ir.md 1.2), so the scan below holds four rules over the
Python source of `sushi_lang/`:

- only `semantics/shir/lowering.py`, the owner of the bodies, makes a `TypeckResults`:
  every other module names the class only as a type (an annotation, a `TypeAlias`, a
  plain import), so an alias, an attribute or a string cannot make a table;
- only the modules of the typecheck pass (`semantics/passes/types/`) name the write API:
  every attribute and every string that spells a write method counts, so a method
  reference, an alias, `getattr`, `map` and `functools.partial` cannot write;
- only `semantics/shir/typeck.py` names the tables that a `TypeckResults` holds;
- a `TypeckResults` has a read API and a write API, and nothing else.

A sample test runs the two scans over a small text of each form. No scan sees the type
of a value, so the table refuses `copy.copy` and `copy.deepcopy` itself. A name that the
code builds at run time (`getattr(t, "record_" + kind)`) is out of reach of a scan.

No module writes a row yet: the typecheck pass starts to write them in P2.4, which adds
the check that the pass calls the write API. The scan reads source; it does not start the
compiler.
"""
from __future__ import annotations

import ast
import copy
import inspect
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

DATA_MODULE = "semantics/shir/typeck.py"
OWNER_MODULE = "semantics/shir/lowering.py"
WRITER_PACKAGE = "semantics/passes/types/"

READ_API = frozenset({
    "type_of", "local_type", "capture_type", "callee_of", "convention_of", "instance_of",
    "field_index_of", "loop_kind_of", "range_value_of", "conversion_of", "try_facts_of",
    "display_type_of", "match_facts_of", "element_type_of",
})
WRITE_API = frozenset({
    "record_type", "record_local", "record_capture", "record_callee", "record_convention",
    "record_instance", "record_field_index", "record_loop_kind", "record_range_value",
    "record_conversion", "record_try", "record_display", "record_match", "record_element",
})
TABLES = frozenset({
    "_node_types", "_local_types", "_capture_types", "_callees", "_arg_conventions",
    "_instance_types", "_field_indices", "_loop_kinds", "_range_values", "_conversions",
    "_try_facts", "_display_types", "_match_facts", "_element_types",
})


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


def _calls_of(names: frozenset[str], modules: dict[str, ast.AST] = MODULES) -> dict[str, list[str]]:
    """For each module, the calls of a name in `names`, as `name:line`."""
    found: dict[str, list[str]] = {}
    for module, tree in modules.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called_name(node) in names:
                found.setdefault(module, []).append(f"{_called_name(node)}:{node.lineno}")
    return found


def _mentions_of(names: frozenset[str], module: str) -> list[str]:
    """Every name or attribute in `names` that `module` holds, as `name:line`."""
    found = []
    for node in ast.walk(MODULES[module]):
        word = (node.id if isinstance(node, ast.Name)
                else node.attr if isinstance(node, ast.Attribute) else None)
        if word in names:
            found.append(f"{word}:{node.lineno}")
    return found


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


def stray_constructions(modules: dict[str, ast.AST]) -> dict[str, list[str]]:
    """Each mention of `TypeckResults` outside its module and its owner that is not a type.

    A plain import of the name is a type use too, so a reader can annotate with it. An
    import under another name is an alias, and the alias can make a table.
    """
    found: dict[str, list[str]] = {}
    for module, tree in modules.items():
        if module in (DATA_MODULE, OWNER_MODULE):
            continue
        types = _annotation_nodes(tree)
        for node in ast.walk(tree):
            if id(node) in types or "TypeckResults" not in _words(node):
                continue
            if isinstance(node, ast.alias) and node.asname is None:
                continue
            found.setdefault(module, []).append(f"TypeckResults:{getattr(node, 'lineno', 0)}")
    return found


def stray_writes(modules: dict[str, ast.AST]) -> dict[str, list[str]]:
    """Each mention of a write-API name outside the typecheck pass and the table module:
    an attribute (a call, an alias, a method reference) or a string (a `getattr`)."""
    found: dict[str, list[str]] = {}
    for module, tree in modules.items():
        if module == DATA_MODULE or module.startswith(WRITER_PACKAGE):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Attribute, ast.Constant)):
                found.setdefault(module, []).extend(
                    f"{word}:{node.lineno}" for word in _words(node) if word in WRITE_API)
    return {module: sites for module, sites in found.items() if sites}


def test_the_owner_of_the_bodies_alone_makes_the_table() -> None:
    stray = stray_constructions(MODULES)
    assert not stray, (
        f"a module outside {OWNER_MODULE} makes a TypeckResults: {stray}. Each body has ONE "
        "table, which lowering lists beside it; read it from the LoweredProgram.")
    calls = _calls_of(frozenset({"TypeckResults"}))
    assert OWNER_MODULE in calls, f"{OWNER_MODULE} makes no TypeckResults; the scan has gone stale"


def test_the_typecheck_pass_alone_calls_the_write_api() -> None:
    stray = stray_writes(MODULES)
    assert not stray, (
        f"a module outside the typecheck pass writes an answer of typecheck: {stray}. "
        "Ruling S5: every answer of typecheck is a row of TypeckResults, and the pass is "
        "its one writer.")


# -- the scan finds each form of a write and of a construction ---------------------------
# Each sample is a module outside the allowed modules. The scan must find each one, so a
# write through an alias, a method reference or a name in a string cannot pass.

_ELSEWHERE = "semantics/elsewhere.py"

WRITE_SAMPLES = {
    "direct": "t.record_type(n, ty)\n",
    "alias": "w = t.record_type\nw(n, ty)\n",
    "getattr": "getattr(t, 'record_type')(n, ty)\n",
    "map": "list(map(t.record_local, locals_, types))\n",
    "partial": "functools.partial(t.record_type, n)(ty)\n",
}

CONSTRUCTION_SAMPLES = {
    "direct": "table = TypeckResults()\n",
    "alias": "Made = TypeckResults\ntable = Made()\n",
    "import alias": "from sushi_lang.semantics.shir.typeck import TypeckResults as Made\n",
    "attribute": "table = typeck.TypeckResults()\n",
    "getattr": "table = getattr(typeck, 'TypeckResults')()\n",
}

#: Text that the scan must let pass: another name with the same start, a write in the
#: typecheck pass, and a reader that names the class in a type alone.
CLEAN_SAMPLES = {
    _ELSEWHERE: ("record_type_site(node)\nself.record_type_site = None\n"
                 "from sushi_lang.semantics.shir.typeck import TypeckResults\n"
                 "def read(t: TypeckResults, n: Optional[TypeckResults]) -> TypeckResults:\n"
                 "    return t\n"
                 "Pair: TypeAlias = tuple[int, Optional[TypeckResults]]\n"
                 "slot: TypeckResults\n"),
    WRITER_PACKAGE + "visit/statements.py": "w = t.record_type\nw(n, ty)\n",
}


@pytest.mark.parametrize("form", sorted(WRITE_SAMPLES))
def test_the_scan_finds_each_form_of_a_write(form: str) -> None:
    assert stray_writes({_ELSEWHERE: ast.parse(WRITE_SAMPLES[form])}), form


@pytest.mark.parametrize("form", sorted(CONSTRUCTION_SAMPLES))
def test_the_scan_finds_each_form_of_a_construction(form: str) -> None:
    assert stray_constructions({_ELSEWHERE: ast.parse(CONSTRUCTION_SAMPLES[form])}), form


def test_the_scan_lets_a_reader_and_the_writer_pass() -> None:
    modules = {module: ast.parse(text) for module, text in CLEAN_SAMPLES.items()}
    assert stray_writes(modules) == {}
    assert stray_constructions(modules) == {}


def test_the_data_module_alone_names_the_tables() -> None:
    stray = {module: _mentions_of(TABLES, module) for module in MODULES
             if module != DATA_MODULE and _mentions_of(TABLES, module)}
    assert not stray, (
        f"a module reads or writes the tables of a TypeckResults past its API: {stray}")
    assert _mentions_of(TABLES, DATA_MODULE), "the tables are gone; the scan has gone stale"


def test_the_table_has_a_read_api_and_a_write_api_and_nothing_else() -> None:
    from sushi_lang.semantics.shir.typeck import TypeckResults

    methods = {name for name, member in inspect.getmembers(TypeckResults, inspect.isfunction)
               if not name.startswith("__")}
    assert methods == READ_API | WRITE_API, (
        f"TypeckResults has the methods {sorted(methods)}; the gate knows "
        f"{sorted(READ_API | WRITE_API)}. A method that writes goes in WRITE_API, so that "
        "the scan holds it to the typecheck pass.")


@pytest.mark.parametrize("copier", [copy.copy, copy.deepcopy], ids=["copy", "deepcopy"])
def test_a_copy_of_a_table_is_refused(copier) -> None:
    """A copy is a second table of one body. No scan can see the type of a copied value,
    so the table refuses the copy itself."""
    from sushi_lang.internals.diagnostics import InternalCompilerError
    from sushi_lang.semantics.shir.typeck import TypeckResults

    with pytest.raises(InternalCompilerError) as caught:
        copier(TypeckResults())
    assert caught.value.code == "CE0000"
