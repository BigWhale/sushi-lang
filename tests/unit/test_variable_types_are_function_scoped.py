"""A function's names do not survive it in `codegen.variable_types`."""
from __future__ import annotations

from llvmlite import ir

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.semantics.typesys import BuiltinType, ReferenceType


def _probe(cg: LLVMCodegen, name: str) -> ir.Function:
    return ir.Function(cg.module, ir.FunctionType(cg.types.i32, []), name=name)


def test_a_function_starts_with_no_inherited_names():
    """An earlier function's names are not visible while a later one is emitted."""
    cg = LLVMCodegen()
    cg.variable_types["v"] = ReferenceType(BuiltinType.I32, "peek")

    cg.functions.helpers.begin_function(_probe(cg, "later"))
    assert "v" not in cg.variable_types, (
        "a stale entry from an earlier function is visible: "
        "`is_reference_parameter` would answer True for a plain local named 'v'"
    )
    cg.functions.helpers.end_function()


def test_names_registered_by_a_function_do_not_outlive_it():
    cg = LLVMCodegen()
    cg.functions.helpers.begin_function(_probe(cg, "first"))
    cg.variable_types["v"] = ReferenceType(BuiltinType.I32, "peek")
    cg.functions.helpers.end_function()

    assert "v" not in cg.variable_types


def test_the_surrounding_map_is_restored_not_discarded():
    """Restore, not clear: nested emission (a lazily emitted out-of-line destructor body) must
    leave the enclosing function's names intact.
    """
    cg = LLVMCodegen()
    cg.variable_types["module_level"] = BuiltinType.I32

    cg.functions.helpers.begin_function(_probe(cg, "inner"))
    cg.variable_types["v"] = BuiltinType.STRING
    cg.functions.helpers.end_function()

    assert cg.variable_types == {"module_level": BuiltinType.I32}
