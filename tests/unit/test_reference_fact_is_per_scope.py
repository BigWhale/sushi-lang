"""The answer to "is this name a reference" belongs to the open scope, not to the name (#941)."""
from __future__ import annotations

from pathlib import Path

from llvmlite import ir

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.backend.expressions.type_utils import is_reference_parameter
from sushi_lang.semantics.passes.collect import PerkImplementationTable
from sushi_lang.semantics.typesys import BorrowMode, BuiltinType, ReferenceType

_BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"


def _codegen_in_a_function() -> LLVMCodegen:
    cg = LLVMCodegen(perk_impl_table=PerkImplementationTable())
    fn = ir.Function(cg.module, ir.FunctionType(cg.types.i32, []), name="probe")
    cg.functions.helpers.begin_function(fn)
    return cg


def _reference_local(cg: LLVMCodegen, name: str) -> None:
    ref = ReferenceType(BuiltinType.I32, BorrowMode.PEEK)
    cg.memory.create_local(name, ir.PointerType(cg.types.i32), None, ref,
                           register_cleanup=False)


def test_a_reference_is_a_reference_while_its_scope_is_open():
    cg = _codegen_in_a_function()
    cg.memory.push_scope()
    _reference_local(cg, "e")
    assert is_reference_parameter(cg, "e")
    cg.memory.pop_scope()
    cg.functions.helpers.end_function()


def test_a_popped_reference_leaves_no_fact_for_a_sibling_of_the_same_name():
    cg = _codegen_in_a_function()
    cg.memory.push_scope()
    _reference_local(cg, "e")
    cg.memory.pop_scope()
    assert not is_reference_parameter(cg, "e")

    cg.memory.push_scope()
    cg.memory.create_local("e", cg.types.i32, None, BuiltinType.I32, register_cleanup=False)
    assert not is_reference_parameter(cg, "e"), (
        "a value binding reads the reference fact of a sibling scope's binding"
    )
    cg.memory.pop_scope()
    cg.functions.helpers.end_function()


def test_an_inner_value_shadow_is_not_a_reference_and_the_outer_one_comes_back():
    cg = _codegen_in_a_function()
    _reference_local(cg, "e")
    cg.memory.push_scope()
    cg.memory.create_local("e", cg.types.i32, None, BuiltinType.I32, register_cleanup=False)
    assert not is_reference_parameter(cg, "e")
    cg.memory.pop_scope()
    assert is_reference_parameter(cg, "e")
    cg.functions.helpers.end_function()


def test_the_name_readers_do_not_read_the_flat_table():
    """The two name readers ask the scope manager; the flat per-function table has no scope."""
    for rel in ("expressions/names.py", "expressions/type_utils.py"):
        source = (_BACKEND / rel).read_text()
        assert "variable_types" not in source, f"{rel} reads the flat `variable_types` table"
