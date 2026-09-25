"""An indexed slot is a fixed array or a dynamic-array descriptor, or it stops (#877).

`emit_element_pointer` bounds-checks an index against the count of its slot. It used to
check only the two slot types it knew, and for any other type it emitted the GEP with no
check at all: a new receiver shape would read out of bounds with no diagnostic. Now the
two-way test is written once and its default arm is the internal error CE0022.
"""
from __future__ import annotations

import pytest
from llvmlite import ir

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.backend.types.arrays import addressing, indexing
from sushi_lang.backend.expressions import type_utils
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.ast import Call, IndexAccess, IntLit, Name
from sushi_lang.semantics.passes.collect import PerkImplementationTable


def _index_into(monkeypatch, slot_type: ir.Type) -> tuple[LLVMCodegen, ir.Function]:
    codegen = LLVMCodegen("index_slot", perk_impl_table=PerkImplementationTable())
    fn = ir.Function(codegen.module, ir.FunctionType(ir.VoidType(), [codegen.i32]), "probe")
    codegen.func = fn
    codegen.builder = ir.IRBuilder(fn.append_basic_block("entry"))
    slot = codegen.builder.alloca(slot_type, name="slot")

    node = IndexAccess(loc=None, array=Call(loc=None, callee=Name(loc=None, id="make"), args=[]),
                       index=IntLit(loc=None, value=0))
    values = {id(node.array): slot, id(node.index): fn.args[0]}
    codegen.expressions.emit_expr = lambda expr, **_: values[id(expr)]  # type: ignore[method-assign]
    monkeypatch.setattr(addressing, "as_array_address", lambda _cg, value, *_a, **_k: value)
    monkeypatch.setattr(type_utils, "infer_expr_semantic_type", lambda _cg, _e: None)
    monkeypatch.setattr(codegen.runtime.errors, "emit_runtime_error_with_values",
                        lambda *_a: None)

    indexing.emit_element_pointer(codegen, node)
    return codegen, fn


def _has_bounds_check(fn: ir.Function) -> bool:
    return any(block.name.endswith("_bounds_fail") for block in fn.blocks)


def test_a_fixed_array_slot_is_bounds_checked(monkeypatch):
    _, fn = _index_into(monkeypatch, ir.ArrayType(ir.IntType(32), 4))
    assert _has_bounds_check(fn)


def test_a_dynamic_array_slot_is_bounds_checked(monkeypatch):
    i32 = ir.IntType(32)
    _, fn = _index_into(monkeypatch, ir.LiteralStructType([i32, i32, i32.as_pointer()]))
    assert _has_bounds_check(fn)


@pytest.mark.parametrize("slot_type", [
    ir.IntType(32),
    ir.IntType(8).as_pointer(),
])
def test_any_other_slot_is_an_internal_error(monkeypatch, slot_type):
    with pytest.raises(InternalCompilerError) as caught:
        _index_into(monkeypatch, slot_type)
    assert caught.value.code == "CE0022"
