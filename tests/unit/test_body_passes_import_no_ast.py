"""The per-body gate: the body modules of `typecheck`, `lift` and `borrow` read SHIR, not the
AST (docs/design/ir.md 6.2 and 6.4).

The scan reads the source of each module of `passes/types/`, `passes/borrow/` and
`passes/lift.py` and finds each import of the AST (`ast_import_scan.py`). A body module
imports none.

The rule has one exception, and `DECLARATION_MODULES` holds it: a module of the
DECLARATION half of `typecheck` may read the declarations of the AST (ir.md 6.1 question
4, 6.4). SHIR has no declaration. Such a module reads a declaration node, a walk of the
declarations, and the types that the source writes (question 1). It never reads an
expression or a statement of a body: `test_no_declaration_row_names_a_body_node` holds
that, with one named exception, the link name of an extern declaration. The row of a
module names each name that it may import, and the scan must find each one, so a row
cannot keep a name that the module no longer imports.
"""
from __future__ import annotations

import typing
from pathlib import Path

from ast_import_scan import ROOT, ast_imports, scan

from sushi_lang.semantics import ast as sushi_ast

PASSES = ROOT / "sushi_lang" / "semantics" / "passes"
_AST = "sushi_lang.semantics.ast"
_WALK = "sushi_lang.semantics.ast_walk"

#: module -> ({AST module: the names it may import}, reason).
DECLARATION_MODULES: dict[str, tuple[dict[str, frozenset[str]], str]] = {
    "sushi_lang/semantics/passes/types/__init__.py": (
        {_AST: frozenset({"ExtendDef", "FuncDef", "Program"})},
        "the driver of the pass: it walks the declarations of a unit and gives each body "
        "to the lowering (ir.md 6.3)"),
    "sushi_lang/semantics/passes/types/constants.py": (
        {_AST: frozenset({"ConstDef", "VarDef"})},
        "the declaration of a constant and of a `var`, and the initializer body of each"),
    "sushi_lang/semantics/passes/types/externals.py": (
        {_AST: frozenset({"ExternalBlock", "ExternalDecl", "MemberAccess", "Program"}),
         _WALK: frozenset({"body_types", "signature_types"})},
        "the extern declarations, the link name that an extern declaration writes, and the "
        "`ptr` fence over the types that the source writes (CE5009)"),
    "sushi_lang/semantics/passes/types/perks.py": (
        {_AST: frozenset({"ExtendWithDef", "FuncDef", "PerkDef", "PerkMethodSignature"}),
         _WALK: frozenset({"is_written", "signature_constraints"})},
        "the headers of a perk implementation against the perk declaration"),
    "sushi_lang/semantics/passes/types/public_signatures.py": (
        {_AST: frozenset({"ExtendWithDef", "Program"}),
         _WALK: frozenset({"ConstraintSite", "TypeSite", "is_written",
                           "signature_constraints", "signature_types"})},
        "the rules of a public signature: the `ptr` quarantine and the private-type leak"),
    "sushi_lang/semantics/passes/types/qualified.py": (
        {_WALK: frozenset({"signature_constraints"})},
        "`check_qualified_constraints`: the qualified perk constraints of the declarations"),
    "sushi_lang/semantics/passes/types/signatures.py": (
        {_AST: frozenset({"ExtendDef", "ExtendWithDef", "FuncDef", "Param", "PerkDef"}),
         _WALK: frozenset({"is_template_copy", "is_written", "signature_types"})},
        "the declared signature of a function and of a method, and its parameters"),
    "sushi_lang/semantics/passes/types/templates.py": (
        {_AST: frozenset({"Block", "BoundedTypeParam", "ExtendDef", "ExtendWithDef",
                          "FuncDef"})},
        "the template check: the header of a template, with an empty body (ir.md 6.2)"),
}

#: A body node that a declaration row may name, with its reason.
DECLARATION_PARTS = {"MemberAccess": "the link name of an extern declaration, `ns.NAME`"}


def _modules() -> list[Path]:
    return [*sorted((PASSES / "types").rglob("*.py")),
            *sorted((PASSES / "borrow").rglob("*.py")), PASSES / "lift.py"]


def _body_node_names() -> set[str]:
    expressions = {cls.__name__ for cls in typing.get_args(sushi_ast.Expr)}
    statements = {name for name, cls in vars(sushi_ast).items()
                  if isinstance(cls, type) and issubclass(cls, sushi_ast.Stmt)}
    return expressions | statements


def test_no_body_module_imports_the_ast():
    found = scan(_modules())
    outside = sorted(path for path in found if path not in DECLARATION_MODULES)
    assert not outside, (
        "a body module of typecheck, lift or borrow imports the AST; it reads SHIR:\n  "
        + "\n  ".join(f"{path}: {sorted((m, sorted(n)) for m, n in found[path].items())}"
                      for path in outside))


def test_each_declaration_module_imports_what_its_row_names():
    found = scan(_modules())
    wrong = {}
    for path, (allowed, _reason) in DECLARATION_MODULES.items():
        imported = {module: frozenset(names) for module, names in found.get(path, {}).items()}
        if imported != allowed:
            wrong[path] = (imported, allowed)
    assert not wrong, (
        "a declaration module imports a name that its row does not name, or no longer "
        "imports a name of its row; change the module or the row:\n  "
        + "\n  ".join(f"{path}: imports {sorted((m, sorted(n)) for m, n in got.items())}, "
                      f"row {sorted((m, sorted(n)) for m, n in row.items())}"
                      for path, (got, row) in sorted(wrong.items())))


def test_no_declaration_row_names_a_body_node():
    body = _body_node_names()
    named = {name for allowed, _reason in DECLARATION_MODULES.values()
             for name in allowed.get(_AST, ())}
    stray = sorted((named & body) - set(DECLARATION_PARTS))
    assert not stray, f"a declaration row names a node of a body: {stray}"
    assert set(DECLARATION_PARTS) <= named, "a DECLARATION_PARTS row names nothing"


def test_the_scan_reads_every_import_form():
    """An import at module level, under TYPE_CHECKING, in a function, and a relative one."""
    module = PASSES / "types" / "probe.py"
    source = ("from typing import TYPE_CHECKING\n"
              "from sushi_lang.semantics import ast\n"
              "if TYPE_CHECKING:\n    from sushi_lang.semantics.ast import Name\n"
              "def f():\n    from ...ast_walk import walk_nodes\n"
              "from sushi_lang.semantics.shir import exprs\n")
    assert ast_imports(module, source) == {_AST: {"*", "Name"}, _WALK: {"walk_nodes"}}
