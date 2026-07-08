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
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type
    codegen.push_string_temp_scope()
    val = codegen.expressions.emit_expr(stmt.value)
    sem = infer_expr_semantic_type(codegen, stmt.value)
    codegen.runtime.formatting.emit_print_value(val, semantic_type=sem)
    codegen.pop_and_free_string_temp_scope()


def emit_println(codegen: 'LLVMCodegen', stmt: 'PrintLn') -> None:
    """Emit println statement using runtime support.

    Evaluates the expression and prints its value to stdout with a newline.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The println statement to emit.
    """
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type
    codegen.push_string_temp_scope()
    val = codegen.expressions.emit_expr(stmt.value)
    sem = infer_expr_semantic_type(codegen, stmt.value)
    codegen.runtime.formatting.emit_print_value(val, is_line=True, semantic_type=sem)
    codegen.pop_and_free_string_temp_scope()
