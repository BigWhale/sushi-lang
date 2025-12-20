"""
Statement emission module for the Sushi language compiler.

This module provides a refactored, modular approach to LLVM IR generation
for statements. The code is organized by statement category for better
maintainability and clarity.

Main entry point: StatementEmitter.emit_stmt()
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.semantics.ast import Stmt
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class StatementEmitter:
    """Main statement emitter that delegates to specialized submodules."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize statement emitter with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and builders.
        """
        self.codegen = codegen

    def emit_stmt(self, stmt: Stmt) -> None:
        """Emit LLVM IR for a statement.

        Dispatches to the appropriate emission method based on statement type.
        Ensures the current block is not terminated before emission.

        Args:
            stmt: The statement AST node to emit.

        Raises:
            NotImplementedError: If the statement type is not supported.
            RuntimeError: If attempting to emit after a terminator.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        self.codegen.utils.ensure_open_block()

        # Import statement types
        from sushi_lang.semantics.ast import (
            Let, Print, PrintLn, Return, If, While, Foreach, Match,
            Break, Continue, Rebind, ExprStmt
        )

        # Delegate to appropriate specialized emitter based on statement type
        match stmt:
            # I/O statements - MIGRATED in Phase 2
            case Print():
                from sushi_lang.backend.statements import io
                return io.emit_print(self.codegen, stmt)
            case PrintLn():
                from sushi_lang.backend.statements import io
                return io.emit_println(self.codegen, stmt)

            # Loop control - MIGRATED in Phase 2
            case Break():
                from sushi_lang.backend.statements import loops
                return loops.emit_break(self.codegen)
            case Continue():
                from sushi_lang.backend.statements import loops
                return loops.emit_continue(self.codegen)

            # Control flow - MIGRATED in Phase 3
            case If():
                from sushi_lang.backend.statements import control_flow
                return control_flow.emit_if(self.codegen, stmt)
            case While():
                from sushi_lang.backend.statements import control_flow
                return control_flow.emit_while(self.codegen, stmt)

            # Return statements - MIGRATED in Phase 3
            case Return():
                from sushi_lang.backend.statements import returns
                return returns.emit_return(self.codegen, stmt)

            # Variable lifecycle - MIGRATED in Phase 4
            case Let():
                from sushi_lang.backend.statements import variables
                return variables.emit_let(self.codegen, stmt)
            case Rebind():
                from sushi_lang.backend.statements import variables
                return variables.emit_rebind(self.codegen, stmt)

            # Expression statements - MIGRATED in Phase 4 (trivial inline)
            case ExprStmt():
                self.codegen.expressions.emit_expr(stmt.expr)
                return

            # Complex loops - MIGRATED in Phase 5
            case Foreach():
                from sushi_lang.backend.statements import loops
                return loops.emit_foreach(self.codegen, stmt)

            # Pattern matching - MIGRATED in Phase 5
            case Match():
                from sushi_lang.backend.statements import matching
                return matching.emit_match(self.codegen, stmt)

            # Unknown statement type
            case _:
                raise NotImplementedError(f"statement not supported yet: {type(stmt).__name__}")

    def emit_block(self, block) -> None:
        """Emit all statements in a block.

        Iterates through statements and emits each one, stopping if a
        terminator is encountered.

        Args:
            block: The block AST node containing statements.
        """
        for stmt in self.codegen.utils.block_statements(block):
            if self.codegen.builder.block.terminator is not None:
                break
            self.emit_stmt(stmt)


__all__ = ['StatementEmitter']
