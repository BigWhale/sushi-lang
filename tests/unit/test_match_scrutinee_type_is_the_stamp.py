"""The backend reads a match scrutinee's enum type from the typecheck stamp alone (#838).

The typecheck pass stamps `Match.resolved_scrutinee_type` on every enum match it
accepts. The backend had a second derivation, `_get_scrutinee_type`, and three lookups
by the pattern's BASE enum name for a missing type. A second answer can disagree with
the first, and a base-name lookup cannot find a monomorphized key. Over the whole
fixture corpus none of them was reached. So a missing stamp is CE0121 at the head of
`emit_match`, and no function below it looks up an enum by name.
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest
from llvmlite import ir

import sushi_lang.backend.statements.matching as matching
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics import ast as sushi_ast

_TREE = ast.parse(pathlib.Path(matching.__file__).read_text(encoding="utf-8"))

# The functions that receive the scrutinee's enum type from emit_match.
_TYPE_READERS = ("_add_switch_cases", "_find_next_arm_with_same_tag",
                 "_extract_pattern_bindings")


def _function(name: str) -> ast.FunctionDef:
    for node in _TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"matching.py declares no {name}()")


def _enum_table_reads(node: ast.AST) -> list[ast.Attribute]:
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and n.attr == "enum_table"]


def test_the_backend_derivation_is_gone():
    assert not hasattr(matching, "_get_scrutinee_type")


@pytest.mark.parametrize("name", _TYPE_READERS)
def test_no_reader_looks_up_an_enum_by_name(name):
    assert _enum_table_reads(_function(name)) == []


def test_the_reader_detector_sees_a_lookup():
    """The control for the row above: a zero from a detector that finds nothing is free."""
    tree = ast.parse("def f(codegen, p):\n    return codegen.enum_table.by_name.get(p)\n")
    assert len(_enum_table_reads(tree)) == 1


def _unstamped_match() -> sushi_ast.Match:
    arm = sushi_ast.MatchArm(
        None, sushi_ast.Pattern(None, "Sign", "Plus", []), sushi_ast.Block(None, []))
    return sushi_ast.Match(None, sushi_ast.Name(None, "s"), [arm])


def _codegen() -> SimpleNamespace:
    module = ir.Module(name="probe")
    fn = ir.Function(module, ir.FunctionType(ir.VoidType(), []), name="probe")
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    enum_value = ir.Constant(ir.LiteralStructType([ir.IntType(32)]), [0])
    return SimpleNamespace(
        builder=builder, func=fn,
        utils=SimpleNamespace(ensure_open_block=lambda: None),
        expressions=SimpleNamespace(emit_expr=lambda _expr: enum_value))


def test_a_missing_stamp_is_ce0121_at_the_match():
    with pytest.raises(InternalCompilerError) as caught:
        matching.emit_match(_codegen(), _unstamped_match())  # type: ignore[arg-type]
    assert caught.value.code == "CE0121"
    assert caught.value.params.get("pattern") == "Sign.Plus"
