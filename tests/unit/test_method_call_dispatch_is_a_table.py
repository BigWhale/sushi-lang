"""The backend method-call dispatch is two ordered tables and one checked-call tail.

The order of the handlers is the method-resolution order (built-in before extension),
so it is data: `PRE_RECEIVER_HANDLERS` run before the receiver is emitted, and
`RECEIVER_HANDLERS` take the emitted receiver. Every declared callee -- a named
function, an extension method, a static method, a perk method -- ends in
`emit_checked_call`, which holds the one arity guard (CE0026).
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from llvmlite import ir

from sushi_lang.backend.expressions.calls import dispatcher, intrinsics
from sushi_lang.internals.diagnostics import InternalCompilerError

CALLS = Path(dispatcher.__file__).parent


def _function_source(module, name: str) -> str:
    return inspect.getsource(getattr(module, name))


def test_the_two_handler_tables_exist_and_hold_callables():
    assert len(dispatcher.PRE_RECEIVER_HANDLERS) == 10
    assert len(dispatcher.RECEIVER_HANDLERS) == 9
    for handler in (*dispatcher.PRE_RECEIVER_HANDLERS, *dispatcher.RECEIVER_HANDLERS):
        assert callable(handler)


def test_the_pre_receiver_handlers_share_one_signature():
    for handler in dispatcher.PRE_RECEIVER_HANDLERS:
        params = list(inspect.signature(handler).parameters)
        assert params == ["codegen", "expr", "to_i1"], handler.__name__


def test_the_receiver_handlers_share_one_signature():
    expected = ["codegen", "expr", "receiver_value", "receiver_type", "semantic_type", "to_i1"]
    for handler in dispatcher.RECEIVER_HANDLERS:
        params = list(inspect.signature(handler).parameters)
        assert params == expected, handler.__name__


def test_emit_method_call_holds_no_hand_written_arm():
    source = _function_source(dispatcher, "emit_method_call")
    assert "if result is not None" not in source


def test_the_arity_guard_is_written_once():
    hits = []
    for path in sorted(CALLS.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if 'raise_internal_error("CE0026"' in line:
                hits.append(f"{path.name}:{lineno}")
    assert len(hits) == 1, hits


def test_every_declared_callee_ends_in_the_checked_tail():
    for module, name in ((dispatcher, "emit_named_call"),
                         (dispatcher, "_emit_extension_call"),
                         (dispatcher, "_try_emit_static_call"),
                         (intrinsics, "try_emit_perk_method")):
        tree = ast.parse(_function_source(module, name).lstrip())
        called = {node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                  for node in ast.walk(tree) if isinstance(node, ast.Call)}
        assert "emit_checked_call" in called, name
        assert "cast_for_param" not in called, name
        assert not re.search(r"builder\.call\(", _function_source(module, name)), name


def test_a_count_mismatch_is_ce0026_and_not_a_bare_value_error():
    module = ir.Module(name="probe")
    fn = ir.Function(module, ir.FunctionType(ir.IntType(32), [ir.IntType(32)] * 2), name="two")
    codegen = SimpleNamespace(builder=None, utils=None)
    with pytest.raises(InternalCompilerError) as caught:
        dispatcher.emit_checked_call(codegen, fn, [ir.Constant(ir.IntType(32), 1)], False)
    assert caught.value.code == "CE0026"
