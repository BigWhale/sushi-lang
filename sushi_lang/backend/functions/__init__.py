"""Unified facade for LLVM function management."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.typesys import Type as Ty

from .helpers import declare_stdlib_function
from .declarations import FunctionDeclarations
from .main_wrapper import MainFunctionWrapper

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.decl_table import FunctionDecl, MethodDecl


class LLVMFunctionManager:
    """Unified facade for LLVM function management."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize function manager with all components."""
        self.codegen = codegen

        self.declarations = FunctionDeclarations(codegen)
        self.main_wrapper = MainFunctionWrapper(codegen)

    def emit_func_decl(self, fn: 'FunctionDecl') -> ir.Function:
        """Create LLVM function prototype for regular function."""
        return self.declarations.emit_func_decl(fn)

    def emit_extension_method_decl(self, ext: 'MethodDecl') -> ir.Function:
        """Create LLVM function prototype for extension method."""
        return self.declarations.emit_extension_method_decl(ext)

    def extract_value_from_result_enum(self, result_enum: ir.Value, value_type: ir.Type,
                                       semantic_type: Ty) -> tuple[ir.Value, ir.Value]:
        """Extract the Ok value from a Result<T> enum."""
        return self.main_wrapper.extract_value_from_result_enum(
            result_enum, value_type, semantic_type
        )


__all__ = ['LLVMFunctionManager', 'declare_stdlib_function']
