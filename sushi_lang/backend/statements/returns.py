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
        # Result.Ok(value) or Result.Err()
        if expr.args:
            # Recursively extract from the first argument
            return _extract_return_variables(expr.args[0])
    elif isinstance(expr, DotCall):
        # DotCall node (unified X.Y(args)) - check if it's an enum constructor
        # For returns, this is typically Result.Ok(value) or Result.Err()
        if expr.args:
            # Recursively extract from the first argument
            return _extract_return_variables(expr.args[0])
    elif isinstance(expr, Name):
        # Simple variable reference
        return {expr.id}

    # Other expressions (literals, method calls, etc.) don't have variables to move
    return set()


def emit_return(codegen: 'LLVMCodegen', stmt: 'Return') -> None:
    """Emit return statement with Result<T> value or bare value for extension methods."""
    # A `return` hands the value to the caller, so the local that produced it must stop
    # owning it -- but this position must NOT decide that on its own. It used to: six
    # ad-hoc type branches that marked the source moved BEFORE the value was emitted. Once
    # the payload position started deciding too (`return Result.Ok(cwd)` consumes `cwd` at
    # ENUM_PAYLOAD), the two derivations fought: the pre-move said "moved, skip cleanup"
    # and the seam said "copy type, clone it", so the original was cloned AND never freed.
    #
    # `Result.Ok(x)` / `Maybe.Some(x)` is already an ENUM_PAYLOAD consuming use, so the
    # whole job here is the shapes that are not: an extension method's bare `return value`.
    # A wrapped return reaches the seam as a FRESH constructor and is a no-op.
    # An extension method returns the bare value; a regular function returns the
    # `Result.Ok(...)` / `Result.Err(...)` the source wrote. Both are just this expression.
    value = codegen.expressions.emit_expr(stmt.value)
    value = _consume_returned_value(codegen, stmt, value)

    # RAII: cleanup for all resources. Ordering is the whole reason RETURN is its own
    # position: the value is emitted and consumed BEFORE cleanup runs, so a MOVE has
    # already flagged the source (cleanup skips it) and a COPY has already taken its
    # independent buffer (cleanup frees the original). Cleaning up first would hand the
    # caller a freed buffer, which is what #256 was.
    from sushi_lang.backend.statements import utils
    utils.emit_scope_cleanup(codegen, cleanup_type='all')

    # Return the value (Result struct for functions, bare value for extension methods)
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
