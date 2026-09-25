"""Unified facade for LLVM function management."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import FuncDef, ExtendDef
from sushi_lang.semantics.typesys import Type as Ty

from .helpers import FunctionHelpers, declare_stdlib_function
from .declarations import FunctionDeclarations
from .definitions import FunctionDefinitions
from .main_wrapper import MainFunctionWrapper

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class LLVMFunctionManager:
    """Unified facade for LLVM function management."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize function manager with all components."""
        self.codegen = codegen

        self.helpers = FunctionHelpers(codegen)
        self.declarations = FunctionDeclarations(codegen)
        self.definitions = FunctionDefinitions(codegen)
        self.main_wrapper = MainFunctionWrapper(codegen)

    def emit_func_decl(self, fn: FuncDef,
                       unit_name: str | None = None) -> ir.Function:
        """Create LLVM function prototype for regular function."""
        return self.declarations.emit_func_decl(fn, unit_name)

    def emit_func_def(self, fn: FuncDef,
                      unit_name: str | None = None) -> ir.Function:
        """Define the body of a regular function."""
        return self.definitions.emit_func_def(fn, unit_name)

    def emit_extension_method_decl(self, ext: ExtendDef) -> ir.Function:
        """Create LLVM function prototype for extension method."""
        return self.declarations.emit_extension_method_decl(ext)

    def emit_extension_method_def(self, ext: ExtendDef) -> ir.Function:
        """Define the body of an extension method."""
        return self.definitions.emit_extension_method_def(ext)

    def extract_value_from_result_enum(self, result_enum: ir.Value, value_type: ir.Type,
                                       semantic_type: Ty) -> tuple[ir.Value, ir.Value]:
        """Extract the Ok value from a Result<T> enum."""
        return self.main_wrapper.extract_value_from_result_enum(
            result_enum, value_type, semantic_type
        )


__all__ = ['LLVMFunctionManager', 'declare_stdlib_function']
