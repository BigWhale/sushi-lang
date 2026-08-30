"""Statement emission module for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.semantics.ast import Stmt
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


import itertools

_EXPR_TEMP_SEQ = itertools.count()


def _register_discarded_owning_temp(codegen: 'LLVMCodegen', expr, value) -> None:
    """Own a discarded expression-statement result that owns heap (#134)."""
    if value is None:
        return
    from sushi_lang.backend.expressions.memory import expression_is_temporary
    if not expression_is_temporary(codegen, expr):
        return
    from sushi_lang.backend.expressions.type_utils import infer_expr_semantic_type
    from sushi_lang.backend.destructors import needs_cleanup, resolve_named_type
    ty = infer_expr_semantic_type(codegen, expr)
    if ty is None and getattr(expr, 'method', None) == 'clone':
        ty = infer_expr_semantic_type(codegen, expr.receiver)
    ty = resolve_named_type(codegen, ty) if ty is not None else None
    if ty is None or not needs_cleanup(codegen, ty):
        return
    name = f"__expr_temp_{next(_EXPR_TEMP_SEQ)}"
    codegen.memory.create_local(name, value.type, value, ty)


class StatementEmitter:
    """Main statement emitter that delegates to specialized submodules."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize statement emitter with reference to main codegen instance."""
        self.codegen = codegen

    def emit_stmt(self, stmt: Stmt) -> None:
        """Emit LLVM IR for a statement."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        self.codegen.utils.ensure_open_block()

        from sushi_lang.semantics.ast import (
            Let, Print, PrintLn, Return, If, While, Foreach, Match,
            Break, Continue, Rebind, ExprStmt
        )

        match stmt:
            case Print():
                from sushi_lang.backend.statements import io
                return io.emit_print(self.codegen, stmt)
            case PrintLn():
                from sushi_lang.backend.statements import io
                return io.emit_println(self.codegen, stmt)

            case Break():
                from sushi_lang.backend.statements import loops
                return loops.emit_break(self.codegen)
            case Continue():
                from sushi_lang.backend.statements import loops
                return loops.emit_continue(self.codegen)

            case If():
                from sushi_lang.backend.statements import control_flow
                return control_flow.emit_if(self.codegen, stmt)
            case While():
                from sushi_lang.backend.statements import control_flow
                return control_flow.emit_while(self.codegen, stmt)

            case Return():
                from sushi_lang.backend.statements import returns
                return returns.emit_return(self.codegen, stmt)

            case Let():
                from sushi_lang.backend.statements import variables
                return variables.emit_let(self.codegen, stmt)
            case Rebind():
                from sushi_lang.backend.statements import variables
                return variables.emit_rebind(self.codegen, stmt)

            case ExprStmt():
                value = self.codegen.expressions.emit_expr(stmt.expr)
                _register_discarded_owning_temp(self.codegen, stmt.expr, value)
                return

            case Foreach():
                from sushi_lang.backend.statements import loops
                return loops.emit_foreach(self.codegen, stmt)

            case Match():
                from sushi_lang.backend.statements import matching
                return matching.emit_match(self.codegen, stmt)

            case _:
                raise NotImplementedError(f"statement not supported yet: {type(stmt).__name__}")

    def emit_block(self, block) -> None:
        """Emit all statements in a block."""
        for stmt in self.codegen.utils.block_statements(block):
            if self.codegen.builder.block.terminator is not None:
                break
            self.emit_stmt(stmt)


__all__ = ['StatementEmitter']
