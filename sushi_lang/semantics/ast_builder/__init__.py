"""
AST Builder module for Sushi language compiler.

Exports:
    ASTBuilder: Main class for building typed AST from Lark parse trees

Build failures are raised as `internals.diagnostics.SushiError` subclasses.
"""
from sushi_lang.semantics.ast_builder.builder import ASTBuilder

__all__ = [
    'ASTBuilder',
]
