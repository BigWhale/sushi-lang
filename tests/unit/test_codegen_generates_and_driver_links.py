"""`LLVMCodegen` generates code; the driver compiles and links (#841).

The backend rule: a stateful subsystem is a manager class composed onto `LLVMCodegen`,
and stateless translation is free functions. The codegen also drove the compiler and
the linker.
"""
from __future__ import annotations

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.backend.driver import LLVMDriver

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

