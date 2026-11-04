"""No definition under `sushi_lang/` lives with no live caller, no template and no row.

vulture sees a name as used when any code names it. So a function that only dead code
names is invisible to it. This scan reads the source alone. It finds each function and
class that the LIVE code reaches over a graph of names: a bare name or an attribute
`x.foo` reaches every definition named `foo`, an `import ... as` alias reaches what it
stands for, and a string that spells a name or a type reads it. The strings of `__all__`
read nothing. The live roots are the top level of every module, a definition that a
decorator registers, the entry points of the two programs, and the `WHITELIST` rows of a
reflective reader.

A port template (a `PORT_TEMPLATES` row of `test_imports_name_what_exists.py`, or a
`WHITELIST` row kept for a later group) is not a root. What a template reaches needs no
row: it belongs to the group of the template, and it dies with it. When the group ports
the template and deletes its row, a definition that only the template reached is
reported here. `KEPT` names the few definitions that neither the live code nor a template
reaches, and that a later group or a reflective reader needs. It may only get shorter.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from test_imports_name_what_exists import PORT_TEMPLATES
from vulture_whitelist import WHITELIST

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "sushi_lang"
ENTRY_POINTS = {("sushi_lang/compiler/__init__.py", "main"),
                ("sushi_lang/packager/__init__.py", "main")}
TEMPLATE_REASONS = ("kept for ", "a port template")
#: A decorator that registers nothing: the definition is reached only by its name.
PLAIN_DECORATORS = frozenset({"dataclass", "property", "staticmethod", "classmethod",
                              "abstractmethod", "cached_property", "contextmanager",
                              "lru_cache", "cache", "overload", "unique", "setter"})
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

#: `path:qualname` -> why it stays: the later group that needs it, or its reflective reader.
KEPT: dict[str, str] = {
    "sushi_lang/backend/generics/hashmap/__init__.py:emit_hashmap_method":
        "kept for B4: the HashMap method family; the SLIR emitter calls it with operands "
        "there, and its AST argument half goes then",
}

#: The rows of `KEPT` at the day of the pin. A row may leave both; none may join.
PINNED_KEPT: frozenset[str] = frozenset({
    "sushi_lang/backend/generics/hashmap/__init__.py:emit_hashmap_method",
})


@dataclass(eq=False)
class Definition:
    path: str
    qualname: str
    refs: set[str]
    registered: bool
    bases: set[str] = field(default_factory=set)
    children: list[Definition] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.qualname.rpartition(".")[2]


def _refs(nodes) -> set[str]:
    """The names that `nodes` read. Of a nested scope, only what its parent evaluates.

    A string that spells a name or a type reads it too: a lookup by name, or a string
    annotation. The strings of `__all__` read nothing.
    """
    found: set[str] = set()
    todo = list(nodes)
    while todo:
        node = todo.pop()
        if isinstance(node, ast.ClassDef):
            todo.extend(node.decorator_list + node.bases + node.keywords)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            todo.extend(node.decorator_list + node.args.defaults)
            todo.extend(d for d in node.args.kw_defaults if d is not None)
        elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "__all__" for t in node.targets):
            continue
        else:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                found.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                found.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                todo.extend(_spelled(node.value))
            todo.extend(ast.iter_child_nodes(node))
    return found


def _spelled(text: str) -> list[ast.AST]:
    """The expression that a string spells, when it spells a name or a type."""
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError:
        return []
    allowed = (ast.Expression, ast.Name, ast.Attribute, ast.Subscript, ast.Tuple,
               ast.BinOp, ast.BitOr, ast.Constant, ast.Load)
    return [tree.body] if all(isinstance(n, allowed) for n in ast.walk(tree)) else []


def _signature_refs(node: ast.AST) -> set[str]:
    """The names that the annotations of a function signature read."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    args = node.args
    every = args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]
    return _refs([a.annotation for a in every if a is not None and a.annotation]
                 + ([node.returns] if node.returns else []))


def _registers(decorator: ast.expr) -> bool:
    node = decorator.func if isinstance(decorator, ast.Call) else decorator
    name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
    return name not in PLAIN_DECORATORS


def _scopes_in(body: list[ast.stmt]):
    """The function and class scopes directly in `body`, through its blocks."""
    todo: list[ast.AST] = list(body)
    while todo:
        node = todo.pop()
        if isinstance(node, _SCOPES):
            yield node
        elif isinstance(node, ast.stmt):
            todo.extend(ast.iter_child_nodes(node))
        elif isinstance(node, (ast.excepthandler, ast.match_case)):
            todo.extend(node.body)


def _definitions(path: str, body: list[ast.stmt], prefix: str,
                 out: list[Definition]) -> list[Definition]:
    found = []
    for node in _scopes_in(body):
        qualname = prefix + node.name
        own = Definition(path, qualname, _refs(node.body) | _signature_refs(node),
                         any(_registers(d) for d in node.decorator_list),
                         _refs(getattr(node, "bases", [])))
        own.children = _definitions(path, node.body, qualname + ".", out)
        out.append(own)
        found.append(own)
    return found


def scan(sources: dict[str, str]):
    """Every definition, the top-level ones, the names the module top levels read, and
    the names that each `import ... as` alias stands for."""
    every: list[Definition] = []
    tops: list[Definition] = []
    names: set[str] = set()
    aliases: dict[str, set[str]] = defaultdict(set)
    for path, source in sources.items():
        tree = ast.parse(source)
        names |= _refs(tree.body)
        tops += _definitions(path, tree.body, "", every)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.asname:
                        aliases[alias.asname].add(alias.name)
    return every, tops, names, aliases


def reach(every: list[Definition], roots: list[Definition], names: set[str],
          aliases: dict[str, set[str]]) -> set[int]:
    """The `id` of each definition that `roots` and the names `names` reach."""
    by_name: dict[str, list[Definition]] = defaultdict(list)
    for d in every:
        by_name[d.name].append(d)
    for alias, originals in aliases.items():
        by_name[alias] += [d for name in originals for d in by_name.get(name, ())]
    seen: set[int] = set()
    todo = list(roots) + [d for n in names for d in by_name.get(n, ())]
    while todo:
        d = todo.pop()
        if id(d) in seen:
            continue
        seen.add(id(d))
        todo.extend(t for n in d.refs for t in by_name.get(n, ()))
        foreign_base = any(b not in by_name for b in d.bases)
        todo.extend(c for c in d.children
                    if foreign_base or c.registered or c.name.startswith("__"))
    return seen


def _key(d: Definition) -> str:
    return f"{d.path}:{d.qualname}"


def _row_matches(path: str, name: str, d: Definition) -> bool:
    return fnmatchcase(d.path, path) and fnmatchcase(d.name, name)


def unexplained(sources: dict[str, str], kept) -> tuple[list[str], set[str]]:
    """The definitions that no live root, no template row and no `kept` row reaches, and
    the definitions that only a `kept` row reaches."""
    every, tops, names, aliases = scan(sources)
    roots = [d for d in tops if d.registered or (d.path, d.qualname) in ENTRY_POINTS]
    roots += [d for d in every if any(_row_matches(p, n, d) for p, n, reason in WHITELIST
                                      if not reason.startswith(TEMPLATE_REASONS))]
    templates = [d for d in every
                 if (d.path.removeprefix("sushi_lang/"), d.qualname) in PORT_TEMPLATES
                 or any(_row_matches(p, n, d) for p, n, reason in WHITELIST
                        if reason.startswith(TEMPLATE_REASONS))]
    explained = reach(every, roots + templates, names, aliases)
    by_kept = reach(every, [d for d in every if _key(d) in kept], set(), aliases)
    return (sorted(_key(d) for d in every if id(d) not in explained | by_kept),
            {_key(d) for d in every if id(d) in by_kept and id(d) not in explained})


def _package_sources() -> dict[str, str]:
    return {path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(PACKAGE.rglob("*.py"))}


def test_no_definition_lives_by_a_template():
    dead, _kept = unexplained(_package_sources(), KEPT)
    assert not dead, (
        f"{len(dead)} definitions have no live caller, and no port template reaches them. "
        "Delete each one, or name it in KEPT (tests/unit/"
        "test_no_definition_lives_by_a_template.py) with the later group that needs it:\n  "
        + "\n  ".join(dead))


def test_every_kept_row_names_a_definition_that_needs_it():
    _dead, kept = unexplained(_package_sources(), KEPT)
    stale = sorted(set(KEPT) - kept)
    assert not stale, f"these KEPT rows name nothing that needs a row; delete them: {stale}"


def test_no_kept_row_is_new():
    grown = sorted(set(KEPT) - PINNED_KEPT)
    assert not grown, f"a KEPT row may be deleted and never added: {grown}"
    stale = sorted(PINNED_KEPT - set(KEPT))
    assert not stale, f"delete the pin row together with the KEPT row: {stale}"


def test_the_scan_reads_each_kind_of_reference():
    source = """
from m import used as alias
__all__ = ["exported"]
def live():
    alias()
    getattr(object, "by_string")
def used(): helper()
def helper(): pass
def by_string(): pass
def exported(): pass
def cycle_a(): cycle_b()
def cycle_b(): cycle_a()
class Visitor(Base):
    def callback(self): pass
TABLE = {"x": live, "v": Visitor}
"""
    dead, _kept = unexplained({"p.py": source}, {})
    assert dead == ["p.py:cycle_a", "p.py:cycle_b", "p.py:exported"]
    _dead, kept = unexplained({"p.py": source}, {"p.py:cycle_a": "kept"})
    assert kept == {"p.py:cycle_a", "p.py:cycle_b"}
