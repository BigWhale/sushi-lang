"""
I/O statement emission for the Sushi language compiler.

This module handles the generation of LLVM IR for print and println statements,
delegating to the runtime support for actual output operations.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Print, PrintLn


def emit_print(codegen: 'LLVMCodegen', stmt: 'Print') -> None:
    """Emit print statement using runtime support.

    Evaluates the expression and prints its value to stdout without a newline.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The print statement to emit.
    """
    val = codegen.expressions.emit_expr(stmt.value)
    codegen.runtime.formatting.emit_print_value(val)


def emit_println(codegen: 'LLVMCodegen', stmt: 'PrintLn') -> None:
    """Emit println statement using runtime support.

    Evaluates the expression and prints its value to stdout with a newline.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The println statement to emit.
    """
    val = codegen.expressions.emit_expr(stmt.value)
    codegen.runtime.formatting.emit_print_value(val, is_line=True)
