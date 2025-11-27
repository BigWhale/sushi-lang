"""
Unified facade for LLVM function management.

This module provides a clean interface to all function-related operations:
declarations, definitions, extension methods, and main function wrapping.

The facade pattern reduces coupling and provides a single entry point for
all function management operations.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from semantics.ast import FuncDef, ExtendDef

from .helpers import FunctionHelpers, declare_stdlib_function
from .declarations import FunctionDeclarations
from .definitions import FunctionDefinitions
from .main_wrapper import MainFunctionWrapper

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class LLVMFunctionManager:
    """
    Unified facade for LLVM function management.

    Provides a single entry point for all function-related operations:
    - Function declarations (prototypes)
    - Function definitions (bodies)
    - Extension methods
    - Main function wrapping for C compatibility

    This facade delegates to specialized components while maintaining
    a simple external interface.
    """

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize function manager with all components.

        Args:
            codegen: The main LLVMCodegen instance.
        """
        self.codegen = codegen

        # Initialize all components
        self.helpers = FunctionHelpers(codegen)
        self.declarations = FunctionDeclarations(codegen)
        self.definitions = FunctionDefinitions(codegen)
        self.main_wrapper = MainFunctionWrapper(codegen)

    # ========================================================================
    # Regular Function Operations
    # ========================================================================

    def emit_func_decl(self, fn: FuncDef) -> ir.Function:
        """Create LLVM function prototype for regular function.

        Args:
            fn: The function definition AST node.

        Returns:
            The LLVM function with declared signature (no body).
        """
        return self.declarations.emit_func_decl(
            fn,
            params_of_fn=self.helpers.params_of,
            helpers=self.helpers
        )

    def emit_func_def(self, fn: FuncDef) -> ir.Function:
        """Define the body of a regular function.

        Args:
            fn: The function definition AST node.

        Returns:
            The LLVM function with body emitted.
        """
        return self.definitions.emit_func_def(
            fn,
            emit_func_decl_fn=self.emit_func_decl,
            begin_function_fn=self.helpers.begin_function,
            end_function_fn=self.helpers.end_function,
            emit_default_return_fn=self.helpers.emit_default_return,
            main_wrapper=self.main_wrapper
        )

    # ========================================================================
    # Extension Method Operations
    # ========================================================================

    def emit_extension_method_decl(self, ext: ExtendDef) -> ir.Function:
        """Create LLVM function prototype for extension method.

        Args:
            ext: The extension method definition AST node.

        Returns:
            The LLVM function with declared signature (no body).
        """
        return self.declarations.emit_extension_method_decl(
            ext,
            get_name_fn=self.helpers.get_extension_method_name
        )

    def emit_extension_method_def(self, ext: ExtendDef) -> ir.Function:
        """Define the body of an extension method.

        Args:
            ext: The extension method definition AST node.

        Returns:
            The LLVM function with body emitted.
        """
        return self.definitions.emit_extension_method_def(
            ext,
            get_name_fn=self.helpers.get_extension_method_name,
            begin_function_fn=self.helpers.begin_function,
            end_function_fn=self.helpers.end_function,
            emit_default_return_for_extension_fn=self.helpers.emit_default_return_for_extension
        )

    # ========================================================================
    # Helper Methods (exposed for backward compatibility)
    # ========================================================================

    def _get_extension_method_name(self, ext: ExtendDef) -> str:
        """Generate unique function name for extension method.

        Args:
            ext: The extension method definition.

        Returns:
            The mangled function name.
        """
        return self.helpers.get_extension_method_name(ext)

    def _extract_value_from_result_enum(self, result_enum, value_type, semantic_type):
        """Extract the Ok value from a Result<T> enum.

        Args:
            result_enum: The Result<T> enum value
            value_type: The LLVM type of T
            semantic_type: The semantic type of T

        Returns:
            Tuple of (is_ok, value)
        """
        return self.main_wrapper.extract_value_from_result_enum(
            result_enum, value_type, semantic_type
        )


# Re-export for backward compatibility
__all__ = ['LLVMFunctionManager', 'declare_stdlib_function']
