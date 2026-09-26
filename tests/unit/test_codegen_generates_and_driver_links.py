"""`LLVMCodegen` generates code; the driver compiles and links, and the print frames are a manager (#841).

The backend rule: a stateful subsystem is a manager class composed onto `LLVMCodegen`,
and stateless translation is free functions. The codegen also drove the compiler and
the linker, and it kept the two print-argument string stacks itself.
"""
from __future__ import annotations

import ast
from pathlib import Path

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.backend.driver import LLVMDriver

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

DRIVER_METHODS = (
    "compile_multi_unit", "compile_to_bitcode", "link_executable",
    "compile_single_unit_to_object", "compile_stdlib_to_object",
    "compile_library_to_object", "link_object_files",
)


def test_the_driver_holds_the_compile_and_link_methods():
    for name in DRIVER_METHODS:
        assert callable(getattr(LLVMDriver, name, None)), name
        assert not hasattr(LLVMCodegen, name), name
    assert not hasattr(LLVMCodegen, "_link_executable")






def _stack_reads(path: Path) -> list[int]:
    return [
        node.lineno for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute) and node.attr in ("_data", "_values")
        and isinstance(node.value, ast.Attribute) and node.value.attr == "print_frames"
    ]


def test_no_module_reaches_into_the_frame_stacks():
    hits = [f"{path.relative_to(ROOT)}:{line}"
            for path in sorted(ROOT.rglob("*.py")) for line in _stack_reads(path)]
    assert hits == []


def test_the_forwarding_properties_are_gone():
    for name in ("printf", "strcmp", "fmt_i32", "fmt_str", "fmt_f32", "fmt_f64"):
        assert not hasattr(LLVMCodegen, name), name
