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
        codegen.print_frames.register_value(val)


def _printed_value(codegen: 'LLVMCodegen', stmt: 'Print | PrintLn') -> 'ir.Value':
    """The value a print writes. A struct, an enum, an array or a container writes its
    `Display` form.

    The typecheck pass stamped the type. The value is only read, so a temporary that
    owns something gets an owner, and the string form is freed with the print frame.
    """
    val = codegen.expressions.emit_expr(stmt.value)
    if stmt.display_type is None:
        _register_owned_string_arg(codegen, stmt.value, val)
        return val
    from sushi_lang.backend.expressions.memory import own_temporary
    from sushi_lang.backend.types.contracts import load_operand
    from sushi_lang.backend.types.display import emit_value_to_str
    val = load_operand(codegen, val, stmt.display_type)
    own_temporary(codegen, stmt.value, val, stmt.display_type)
    text = emit_value_to_str(codegen, val, stmt.display_type)
    codegen.print_frames.register_value(text)
    return text


def emit_print(codegen: 'LLVMCodegen', stmt: 'Print') -> None:
    """Emit print statement using runtime support."""
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type
    codegen.print_frames.push()
    val = _printed_value(codegen, stmt)
    sem = infer_expr_semantic_type(codegen, stmt.value)
    codegen.runtime.formatting.emit_print_value(val, semantic_type=sem)
    codegen.print_frames.pop_and_free()


def emit_println(codegen: 'LLVMCodegen', stmt: 'PrintLn') -> None:
    """Emit println statement using runtime support."""
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type
    codegen.print_frames.push()
    val = _printed_value(codegen, stmt)
    sem = infer_expr_semantic_type(codegen, stmt.value)
    codegen.runtime.formatting.emit_print_value(val, is_line=True, semantic_type=sem)
    codegen.print_frames.pop_and_free()
