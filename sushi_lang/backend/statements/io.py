"""I/O statement emission for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Print, PrintLn


def _register_owned_string_arg(codegen: 'LLVMCodegen', expr, val: 'ir.Value') -> None:
    """Register an owned string TEMPORARY print argument for the frame's guarded free.

    An INTERPOLATION belongs here like any other temporary. It used to be excluded
    because the buffers it built registered with THIS frame, which also claimed the ones
    built for a NESTED call's argument -- a second owner beside the one the call site had
    already given them, and exit 133 (#521). An interpolation opens a frame of its own
    now, so what registers here is the argument print actually holds. A LITERAL stays
    out: it owns no heap.
    """
    from sushi_lang.semantics.ast import StringLit
    if isinstance(expr, StringLit):
        return
    if not codegen.types.is_string_type(val.type):
        return
    from sushi_lang.backend.expressions.memory import expression_is_temporary
    if expression_is_temporary(codegen, expr):
        codegen.register_string_value_temp(val)


def emit_print(codegen: 'LLVMCodegen', stmt: 'Print') -> None:
    """Emit print statement using runtime support."""
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type
    codegen.push_string_temp_scope()
    val = codegen.expressions.emit_expr(stmt.value)
    _register_owned_string_arg(codegen, stmt.value, val)
    sem = infer_expr_semantic_type(codegen, stmt.value)
    codegen.runtime.formatting.emit_print_value(val, semantic_type=sem)
    codegen.pop_and_free_string_temp_scope()


def emit_println(codegen: 'LLVMCodegen', stmt: 'PrintLn') -> None:
    """Emit println statement using runtime support."""
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type
    codegen.push_string_temp_scope()
    val = codegen.expressions.emit_expr(stmt.value)
    _register_owned_string_arg(codegen, stmt.value, val)
    sem = infer_expr_semantic_type(codegen, stmt.value)
    codegen.runtime.formatting.emit_print_value(val, is_line=True, semantic_type=sem)
    codegen.pop_and_free_string_temp_scope()
