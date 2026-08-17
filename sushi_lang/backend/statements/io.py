"""
I/O statement emission for the Sushi language compiler.

This module handles the generation of LLVM IR for print and println statements,
delegating to the runtime support for actual output operations.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Print, PrintLn


def _register_owned_string_arg(codegen: 'LLVMCodegen', expr, val: 'ir.Value') -> None:
    """Register an owned string TEMPORARY print argument for the frame's guarded free.

    An owned string that no binding names (`println(go().realise("err"))`, a `??` unwrap,
    a string-method result) had no owner at all and leaked. The print-arg
    frame frees it right after output through the owned-bit-guarded destructor, so a
    borrow-shaped value (owned=0) frees to a no-op.

    A `Name` / field read / container get-out is a BORROW (its owner frees it) and
    `expression_is_temporary` answers False for those. An `InterpolatedString` manages its
    own buffers: its concat output registers into the frame's data-pointer half
    (runtime/strings.py) and its parts register themselves (expressions/literals.py), so
    registering the whole value here too would double-free.
    """
    from sushi_lang.semantics.ast import InterpolatedString, StringLit
    if isinstance(expr, (InterpolatedString, StringLit)):
        return
    if not codegen.types.is_string_type(val.type):
        return
    from sushi_lang.backend.expressions.memory import expression_is_temporary
    if expression_is_temporary(codegen, expr):
        codegen.register_string_value_temp(val)


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
    _register_owned_string_arg(codegen, stmt.value, val)
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
    _register_owned_string_arg(codegen, stmt.value, val)
    sem = infer_expr_semantic_type(codegen, stmt.value)
    codegen.runtime.formatting.emit_print_value(val, is_line=True, semantic_type=sem)
    codegen.pop_and_free_string_temp_scope()
