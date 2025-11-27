"""
Function definition (body emission) for LLVM code generation.

This module handles emitting the actual function bodies for both regular
Sushi functions and extension methods, including special handling for main().
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from semantics.ast import FuncDef, ExtendDef
from internals.errors import raise_internal_error

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class FunctionDefinitions:
    """Handles LLVM function body emission."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize definitions handler with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance.
        """
        self.codegen = codegen

    def emit_func_def(
        self,
        fn: FuncDef,
        emit_func_decl_fn,
        begin_function_fn,
        end_function_fn,
        emit_default_return_fn,
        main_wrapper
    ) -> ir.Function:
        """Define the body of a regular function.

        Args:
            fn: The function definition AST node.
            emit_func_decl_fn: Function to emit declaration.
            begin_function_fn: Function to begin function emission.
            end_function_fn: Function to end function emission.
            emit_default_return_fn: Function to emit default return.
            main_wrapper: MainFunctionWrapper instance.

        Returns:
            The LLVM function with body emitted.

        Raises:
            TypeError: If the return type is not supported.
        """
        # Special handling for main function - needs wrapper for C compatibility
        if fn.name == 'main':
            if self.codegen.main_expects_args:
                return main_wrapper.emit_main_with_args(
                    fn, begin_function_fn, end_function_fn,
                    lambda f: main_wrapper.create_user_main_function(
                        f, lambda x: self.codegen.functions.helpers.params_of(x),
                        begin_function_fn, end_function_fn, emit_default_return_fn
                    )
                )
            else:
                return main_wrapper.emit_main_without_args(
                    fn, begin_function_fn, end_function_fn,
                    lambda f: main_wrapper.create_user_main_function(
                        f, lambda x: self.codegen.functions.helpers.params_of(x),
                        begin_function_fn, end_function_fn, emit_default_return_fn
                    )
                )

        # Normal function emission
        llvm_fn = self.codegen.funcs.get(fn.name) or emit_func_decl_fn(fn)
        # Pass fn to _begin_function so it can register parameter semantic types
        begin_function_fn(llvm_fn, fn)

        # Track current function AST for ?? operator (needs return type info)
        self.codegen.current_function_ast = fn

        # Track parameter types in variable_types for struct member access resolution
        for param in fn.params:
            if param.ty is not None:
                self.codegen.variable_types[param.name] = param.ty

        self.codegen.statements.emit_block(fn.body)

        if self.codegen.builder.block.terminator is None:
            emit_default_return_fn(fn.ret)

        end_function_fn()

        # Clear function AST after emission
        self.codegen.current_function_ast = None

        return llvm_fn

    def emit_extension_method_def(
        self,
        ext: ExtendDef,
        get_name_fn,
        begin_function_fn,
        end_function_fn,
        emit_default_return_for_extension_fn
    ) -> ir.Function:
        """Define the body of an extension method.

        Args:
            ext: The extension method definition AST node.
            get_name_fn: Function to get extension method name.
            begin_function_fn: Function to begin function emission.
            end_function_fn: Function to end function emission.
            emit_default_return_for_extension_fn: Function to emit default return for extension.

        Returns:
            The LLVM function with body emitted.

        Raises:
            RuntimeError: If the extension method was not declared.
            TypeError: If the return type is not supported.
        """
        func_name = get_name_fn(ext)
        llvm_fn = self.codegen.funcs.get(func_name)
        if not llvm_fn:
            raise_internal_error("CE0025", name=func_name)

        # Extension methods use ExtendDef, so we can't pass it as FuncDef
        # For now, don't pass semantic types for extension methods
        # (they typically don't pattern match on their parameters)
        begin_function_fn(llvm_fn)

        # Mark that we're compiling an extension method (for return handling)
        self.codegen.in_extension_method = True

        # Track 'self' and parameter types in variable_types for struct member access resolution
        if ext.target_type is not None:
            self.codegen.variable_types["self"] = ext.target_type
        for param in ext.params:
            if param.ty is not None:
                self.codegen.variable_types[param.name] = param.ty

        self.codegen.statements.emit_block(ext.body)

        if self.codegen.builder.block.terminator is None:
            emit_default_return_for_extension_fn(ext.ret)

        self.codegen.in_extension_method = False
        end_function_fn()
        return llvm_fn
