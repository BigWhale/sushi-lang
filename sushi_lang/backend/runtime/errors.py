"""Runtime error handling for LLVM code generation."""
from __future__ import annotations

import typing

from llvmlite import ir

from sushi_lang.backend.constants.llvm_values import ZERO_I32
from sushi_lang.backend.functions.signature import call_signature
from sushi_lang.backend.string_constants import pct_s_format, runtime_error_text

if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class RuntimeErrors:
    """Manages runtime error emission and errno handling."""

    def __init__(self, codegen: LLVMCodegen) -> None:
        """Initialize with reference to main codegen instance."""
        self.codegen = codegen

    def emit_runtime_error(self, error_code: str, **params) -> None:
        """Emit runtime error message to stderr and exit program."""
        self._call_shared_exit(runtime_error_text(self.codegen.module, error_code, **params))

    def emit_runtime_error_with_values(
        self, error_code: str, *values: ir.Value
    ) -> None:
        """Emit runtime error message with formatted values to stderr and exit program."""
        text = runtime_error_text(self.codegen.module, error_code)
        if not values:
            self._call_shared_exit(text)
            return
        builder = self.codegen.builder
        fmt_ptr = builder.gep(text, [ZERO_I32, ZERO_I32], name="err_fmt_ptr")
        stderr_ptr = builder.load(self.codegen.runtime.libc_stdio.stderr_handle, name="stderr")
        builder.call(self.codegen.runtime.libc_stdio.fprintf, [stderr_ptr, fmt_ptr] + list(values))
        builder.call(self.codegen.runtime.libc_process.exit, [ir.Constant(self.codegen.i32, 1)])

    def _call_shared_exit(self, text: ir.GlobalVariable) -> None:
        """A message with no run-time value is printed by ONE private function for each
        text, so a trap site is one call and not a copy of the print and the exit."""
        self.codegen.builder.call(self._shared_exit(text), [])

    def _shared_exit(self, text: ir.GlobalVariable) -> ir.Function:
        module = self.codegen.module
        name = f"__sushi_exit_{text.name}"
        existing = module.globals.get(name)
        if existing is not None:
            return existing
        fn = ir.Function(module, call_signature(ir.VoidType(), []).function_type, name=name)
        fn.linkage = "private"
        fn.attributes.add("noreturn")
        fn.attributes.add("cold")
        fn.attributes.add("noinline")
        fn.attributes.add("nounwind")
        builder = ir.IRBuilder(fn.append_basic_block("entry"))
        msg_ptr = builder.gep(text, [ZERO_I32, ZERO_I32], name="err_msg_ptr")
        stderr_ptr = builder.load(self.codegen.runtime.libc_stdio.stderr_handle, name="stderr")
        pct_s_ptr = builder.gep(pct_s_format(module), [ZERO_I32, ZERO_I32], name="pct_s_ptr")
        builder.call(self.codegen.runtime.libc_stdio.fprintf, [stderr_ptr, pct_s_ptr, msg_ptr])
        builder.call(self.codegen.runtime.libc_process.exit, [ir.Constant(self.codegen.i32, 1)])
        builder.unreachable()
        return fn
