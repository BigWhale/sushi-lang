"""
Function declaration handling for LLVM code generation.

This module handles the creation of LLVM function prototypes (signatures)
for both regular Sushi functions and extension methods, without emitting
function bodies.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import FuncDef, ExtendDef

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class FunctionDeclarations:
    """Handles LLVM function prototype generation."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize declarations handler with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance.
        """
        self.codegen = codegen

    def emit_func_decl(self, fn: FuncDef, params_of_fn, helpers) -> ir.Function:
        """Create LLVM function prototype for regular function.

        Args:
            fn: The function definition AST node.
            params_of_fn: Function to extract parameters.
            helpers: FunctionHelpers instance.

        Returns:
            The LLVM function with declared signature (no body).
        """
        existing = self.codegen.funcs.get(fn.name)
        if existing is not None:
            return existing

        # Special handling for main function - it needs C-compatible signature
        # Main always needs a wrapper because Sushi functions return Result<T>
        # but C expects int main()
        # Skip wrapper in library mode (main is just a regular function)
        if fn.name == 'main' and not getattr(self.codegen, 'is_library_mode', False):
            if self.codegen.main_expects_args:
                # Generate C-style main signature: int main(int argc, char** argv)
                ll_param_tys = [
                    self.codegen.types.i32,                                    # argc: int
                    ir.PointerType(ir.PointerType(self.codegen.types.i8))     # argv: char**
                ]
            else:
                # Generate C-style main signature: int main()
                ll_param_tys = []

            ll_ret = self.codegen.types.i32  # main always returns int in C
            fnty = ir.FunctionType(ll_ret, ll_param_tys)
            llvm_fn = ir.Function(self.codegen.module, fnty, name=fn.name)

            if self.codegen.main_expects_args:
                # Set parameter names for clarity
                llvm_fn.args[0].name = "argc"
                llvm_fn.args[1].name = "argv"
        else:
            # Normal Sushi function signature
            # All functions now return Result<T> where T is fn.ret
            params = params_of_fn(fn)
            ll_param_tys = [self.codegen.types.ll_type(ty) for _, ty in params]
            from sushi_lang.semantics.typesys import GenericTypeRef

            # Check if return type is already explicit Result<T, E>
            from sushi_lang.semantics.generics.results import is_result_enum
            from sushi_lang.backend.generics.result_builder import implicit_result_of

            # An explicit `fn foo() Result<T, E>` is used as-is; anything else is implicitly
            # wrapped. The interned enum counts as explicit -- wrapping it again would produce
            # Result<Result<T, E>, StdError>.
            is_explicit_result = (
                is_result_enum(fn.ret) or
                (isinstance(fn.ret, GenericTypeRef) and fn.ret.base_name == "Result")
            )

            result_ty = fn.ret if is_explicit_result else implicit_result_of(self.codegen, fn)
            ll_ret = self.codegen.types.ll_type(result_ty)

            fnty = ir.FunctionType(ll_ret, ll_param_tys)
            llvm_fn = ir.Function(self.codegen.module, fnty, name=fn.name)

            # Set Sushi parameter names
            for i, (pname, _) in enumerate(params):
                llvm_fn.args[i].name = pname

        # Set linkage based on visibility:
        # - main function (not in library mode): always external linkage (required by linker)
        # - public functions: external linkage (accessible across units and for linking)
        # - private functions: internal linkage (only accessible within this module)
        is_library_mode = getattr(self.codegen, 'is_library_mode', False)
        if fn.name == 'main' and not is_library_mode:
            llvm_fn.linkage = 'external'
        else:
            llvm_fn.linkage = 'external' if fn.is_public else 'internal'

        self.codegen.funcs[fn.name] = llvm_fn

        # Store the semantic return type for Result<T, E> type inference
        # This helps when inferring Result<T, E> types from function call expressions
        if fn.name != 'main' and fn.ret is not None:
            is_explicit_result = (
                is_result_enum(fn.ret) or
                (isinstance(fn.ret, GenericTypeRef) and fn.ret.base_name == "Result")
            )
            self.codegen.function_return_types[fn.name] = (
                fn.ret if is_explicit_result else implicit_result_of(self.codegen, fn)
            )

        return llvm_fn

    def emit_extension_method_decl(self, ext: ExtendDef, get_name_fn) -> ir.Function:
        """Create LLVM function prototype for extension method.

        Args:
            ext: The extension method definition AST node.
            get_name_fn: Function to get extension method name.

        Returns:
            The LLVM function with declared signature (no body).
        """
        func_name = get_name_fn(ext)

        param_types = []
        param_names = []

        if ext.target_type:
            param_types.append(self.codegen.types.ll_type(ext.target_type))
            param_names.append("self")

        for param in ext.params:
            if param.ty:
                param_types.append(self.codegen.types.ll_type(param.ty))
                param_names.append(param.name)

        # Extension methods return bare types (not Result<T>)
        # This matches built-in extension methods and provides zero-cost abstraction
        if ext.ret:
            ret_type = self.codegen.types.ll_type(ext.ret)
        else:
            ret_type = ir.VoidType()

        func_type = ir.FunctionType(ret_type, param_types)
        llvm_fn = ir.Function(self.codegen.module, func_type, name=func_name)

        for i, name in enumerate(param_names):
            if i < len(llvm_fn.args):
                llvm_fn.args[i].name = name

        self.codegen.funcs[func_name] = llvm_fn
        return llvm_fn
