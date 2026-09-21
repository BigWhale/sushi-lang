"""`matching.py` resolves a type name in ONE place (#742).

The file resolved an `UnknownType` by hand nine times, and named
`validator.struct_table.by_name` and `validator.enum_table.by_name` at every one of
them plus at the scrutinee, behind seven function-level `from ... import UnknownType`
lines that re-imported a name the module header already held. A copy is a place where
the next reader forgets a table, and a third table would have had to be added ten
times.

The idiom lives in `_resolve` now, the table pair is spelled once in
`_resolve_through_tables`, and the three arm-body reads live in `_walk_arm_body`.
"""
from __future__ import annotations

import ast
import pathlib

import sushi_lang.semantics.passes.types.matching as matching

_SOURCE = pathlib.Path(matching.__file__).read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _calls(name: str) -> list[ast.Call]:
    return [node for node in ast.walk(_TREE)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name]


def _function(name: str) -> ast.FunctionDef:
    for node in _TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"matching.py declares no {name}()")


def _reads(table: str) -> list[ast.Attribute]:
    """Every `<something>.<table>.by_name` read in the file."""
    return [node for node in ast.walk(_TREE)
            if isinstance(node, ast.Attribute) and node.attr == "by_name"
            and isinstance(node.value, ast.Attribute) and node.value.attr == table]


def test_the_table_pair_is_named_once():
    assert len(_reads("struct_table")) == 1, len(_reads("struct_table"))
    assert len(_reads("enum_table")) == 1, len(_reads("enum_table"))


def test_the_table_pair_is_named_inside_the_seam():
    seam = _function("_resolve_through_tables")
    inside = {id(node) for node in ast.walk(seam)}
    for table in ("struct_table", "enum_table"):
        assert id(_reads(table)[0]) in inside, table


def test_the_resolver_is_called_from_one_place():
    assert len(_calls("resolve_unknown_type")) == 1, len(
        _calls("resolve_unknown_type"))


def test_the_seam_functions_exist():
    for name in ("_resolve", "_resolve_through_tables", "_walk_arm_body"):
        assert hasattr(matching, name), name


def test_no_import_hides_inside_a_function_body():
    """Every import this module needs stands at the top of it.

    Seven of them re-imported `UnknownType`, which the module header already held.
    One re-imported `BorrowMode` and `ReferenceType` twice, and one re-imported
    `RefBinding`. The two that stay are the ones that would close an import cycle.
    """
    hidden = [alias.name
              for node in _TREE.body if isinstance(node, ast.FunctionDef)
              for inner in ast.walk(node)
              if isinstance(inner, (ast.Import, ast.ImportFrom))
              for alias in inner.names]
    assert sorted(hidden) == ["int_literal_fits", "reject_qualified_name"], hidden


def test_the_arm_body_is_walked_from_one_place():
    walker = _function("_walk_arm_body")
    shapes = [node for node in ast.walk(_TREE)
              if isinstance(node, ast.Attribute) and node.attr == "body"
              and isinstance(node.value, ast.Name) and node.value.id == "arm"]
    inside = {id(node) for node in ast.walk(walker)}
    assert shapes, "no arm body is read at all"
    assert all(id(node) in inside for node in shapes), len(shapes)
    assert len(_calls("_walk_arm_body")) == 3, len(_calls("_walk_arm_body"))
