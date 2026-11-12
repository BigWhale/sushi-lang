"""An internal error of the SLIR emitter writes nothing (docs/design/ir.md).

A shape with no row is an internal error before the statement stores anything, and both build paths build
every module before they write an object. The tests give the emitter a hand-built statement
below its stage entry, and read the Python source of the two paths. They do not run the
compiler.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest
from llvmlite import ir

from sushi_lang.backend.driver import LLVMDriver
from sushi_lang.backend.slir import emit
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.compiler import pipeline
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.function import SlirFunction
from sushi_lang.semantics.slir.ids import BlockId, LocalId

SPAN = Span(3, 5, 3, 9)


class _NoRow:
    """A SLIR shape that no table of the emitter holds."""


def _state() -> FunctionState:
    module = ir.Module(name="stop")
    llvm_fn = ir.Function(module, ir.FunctionType(ir.VoidType(), []), name="stop$f")
    entry = llvm_fn.append_basic_block(name="entry")
    function = SlirFunction("stop$f", "f", "stop", (), (), (), BlockId(0))
    state = FunctionState(SimpleNamespace(), function, llvm_fn, ir.IRBuilder(entry), "stop.sushi")
    state.span = SPAN
    return state


@pytest.mark.parametrize("statement", [
    st.Assign(p.Local(LocalId(0)), _NoRow(), SPAN),  # type: ignore[arg-type]
    _NoRow(),
])
def test_a_shape_with_no_row_is_an_internal_error_before_it_stores(statement):
    state = _state()
    with pytest.raises(InternalCompilerError) as fault:
        emit._statement(state, statement)
    assert fault.value.code == "CE0000"
    assert "'_NoRow'" in fault.value.params["detail"]
    assert fault.value.span == SPAN
    assert state.llvm_fn.entry_basic_block.instructions == []


def _calls_in_order(function: object) -> list[str]:
    tree = ast.parse(inspect.getsource(function).lstrip())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)]
    return [call.func.attr for call in sorted(calls, key=lambda call: call.lineno)]


def test_the_module_path_builds_every_unit_before_it_writes():
    calls = _calls_in_order(pipeline._compile_incremental)
    assert calls.index("build_module_single_unit") < calls.index("store_unit_object")


def test_the_whole_program_path_builds_before_it_writes():
    calls = _calls_in_order(LLVMDriver.compile_multi_unit)
    assert calls.index("build_module_multi_unit") < calls.index("link_executable")
