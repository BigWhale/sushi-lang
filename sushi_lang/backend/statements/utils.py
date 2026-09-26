"""Shared utilities for statement emission in the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_condition(codegen: 'LLVMCodegen', expr) -> 'ir.Value':
    """Emit a boolean condition.

    A condition is a bool since #522, so no wrapper temporary can stand here any more.
    The Result/Maybe temporary this used to free (#159) is now behind the `.is_ok()`
    that tests it, where the ordinary method-call temp registry owns it.
    """
    return codegen.utils.as_i1(codegen.expressions.emit_expr(expr))


def emit_scope_cleanup(codegen: 'LLVMCodegen') -> None:
    """Emit the destructors of every open scope on a function exit (return, `??`)."""
    codegen.memory.emit_exit_cleanup(0)

    # A `??` inside a print argument leaves through here, not the frame's straight-line
    # pop, so the buffers built before the propagation had no free at all (#295).
    codegen.print_frames.free_all_on_early_exit()
