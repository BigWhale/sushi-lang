"""The optimizer pipelines are one table (#882).

`_PIPELINES` in `backend/llvm_optimization.py` maps each `--opt` level to its speed level
and its pass lists, and one builder reads it. O3 extends O2: every O2 pass is in O3, in the
O2 order. A mode with no row is an internal error, never an empty pipeline.
"""
import ast
from pathlib import Path

import pytest
from llvmlite import binding as llvm

from sushi_lang.backend import llvm_optimization
from sushi_lang.backend.llvm_optimization import _PIPELINES, LLVMOptimizer
from sushi_lang.internals.diagnostics import InternalCompilerError


def _is_subsequence(small, big) -> bool:
    remaining = iter(big)
    return all(any(item is other for other in remaining) for item in small)


def test_the_table_covers_every_standard_level():
    assert set(_PIPELINES) == {"o1", "o2", "o3"}
    assert [_PIPELINES[k].speed_level for k in ("o1", "o2", "o3")] == [1, 2, 3]


def test_o3_holds_o2_in_its_order():
    o2, o3 = _PIPELINES["o2"], _PIPELINES["o3"]
    assert _is_subsequence(o2.function_passes, o3.function_passes)
    assert _is_subsequence(o2.module_passes, o3.module_passes)


@pytest.mark.parametrize("level", sorted(_PIPELINES))
def test_no_module_pass_is_repeated(level):
    passes = _PIPELINES[level].module_passes
    assert len(set(passes)) == len(passes)


def test_an_unknown_mode_is_an_internal_error():
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()
    module = llvm.parse_assembly("define i32 @f() {\n  ret i32 0\n}\n")
    tm = llvm.Target.from_default_triple().create_target_machine()
    with pytest.raises(InternalCompilerError) as caught:
        LLVMOptimizer._apply_standard_optimization(module, tm, "o4")
    assert caught.value.code == "CE0000"


def test_the_builder_is_one():
    tree = ast.parse(Path(llvm_optimization.__file__).read_text(encoding="utf-8"))
    builders = [node.name for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name.startswith("_build_o")]
    assert builders == []
