"""Return statement emission for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import Return, Expr


def _extract_return_variables(expr: 'Expr') -> set[str]:
    """Extract variable names from return expression."""
    from sushi_lang.semantics.ast import EnumConstructor, DotCall, Name

    if isinstance(expr, EnumConstructor):
        if expr.args:
            return _extract_return_variables(expr.args[0])
    elif isinstance(expr, DotCall):
        if expr.args:
            return _extract_return_variables(expr.args[0])
    elif isinstance(expr, Name):
        return {expr.id}

    return set()


def emit_return(codegen: 'LLVMCodegen', stmt: 'Return') -> None:
    """Emit return statement with Result<T> value or bare value for extension methods."""
    # `Result.Ok(x)` is already an ENUM_PAYLOAD consuming use, so the job here is the
    # shapes that are not: an extension method's bare `return value`. This position must
    # NOT decide ownership on its own -- marking the source moved before the value was
    # emitted fought the payload position, and the original was cloned AND never freed.
    value = codegen.expressions.emit_expr(stmt.value)
    value = _consume_returned_value(codegen, stmt, value)

    # ORDERING is the whole reason RETURN is its own position: the value is emitted and
    # consumed BEFORE cleanup, so a MOVE has already flagged the source. Cleaning up first
    # hands the caller a freed buffer (#256).
    from sushi_lang.backend.statements import utils
    utils.emit_scope_cleanup(codegen, cleanup_type='all')

    codegen.builder.ret(value)


def _consume_returned_value(codegen: 'LLVMCodegen', stmt: 'Return',
                            value: 'ir.Value') -> 'ir.Value':
    """Route a bare returned value through the ownership seam."""
    from sushi_lang.semantics.ast import Name
    from sushi_lang.backend.ownership import ConsumingUse, consume

    if not isinstance(stmt.value, Name):
        return value
    semantic_type = codegen.memory.find_semantic_type(stmt.value.id)
    return consume(codegen, stmt.value, value, semantic_type, ConsumingUse.RETURN)
