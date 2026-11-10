"""Function declaration handling for LLVM code generation."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.unit_symbols import emitted_symbol
from sushi_lang.backend.generics.result_builder import declared_return_ll

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.decl_table import FunctionDecl, MethodDecl


class FunctionDeclarations:
    """Handles LLVM function prototype generation."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize declarations handler with reference to main codegen instance."""
        self.codegen = codegen

    def emit_func_decl(self, fn: 'FunctionDecl') -> ir.Function:
        """Create LLVM function prototype for regular function.

        The record names the unit whose declaration this is, and its symbol
        (`function_symbol`, the one derivation that SLIR lowering reads too): two units may
        each declare a private `helper`, and the monolithic path puts both into one module.
        A synthesized body -- a monomorphized instance, a lifted lambda -- goes home to the
        unit of its template, or keeps the bare name it was given (`declaring_unit`).

        The body of a function named `main` is `user_main` (`function_symbol`), with the
        declared signature. In a program, `main` itself is the C entry `int main(int,
        char**)`, which calls it. A library declares no `main` (the entrypoint pass).
        """
        existing = self.codegen.funcs.declared(fn.name, fn.unit)
        if existing is not None:
            return existing

        ll_param_tys = [self.codegen.types.ll_type(ty) for _, ty in fn.params]
        fnty = ir.FunctionType(declared_return_ll(self.codegen, fn), ll_param_tys)
        llvm_fn = ir.Function(self.codegen.module, fnty, name=fn.symbol)
        for i, (pname, _) in enumerate(fn.params):
            llvm_fn.args[i].name = pname

        # Set linkage based on visibility:
        # - public functions: external linkage (accessible across units and for linking)
        # - private functions and the body of main: internal linkage
        is_entry = fn.name == 'main' and not getattr(self.codegen, 'is_library_mode', False)
        llvm_fn.linkage = 'external' if fn.is_public and not is_entry else 'internal'

        if is_entry:
            llvm_fn = self._declare_c_main(emitted_symbol(fn.name, fn.unit))

        self.codegen.funcs.declare(fn.name, llvm_fn, unit=fn.unit)
        return llvm_fn

    def _declare_c_main(self, symbol: str) -> ir.Function:
        """The C entry: `int main(int, char**)` when the program's main takes `string[]
        args`, else `int main()`. External, because the linker needs it."""
        if self.codegen.main_expects_args:
            ll_param_tys = [
                self.codegen.types.i32,                                    # argc: int
                ir.PointerType(ir.PointerType(self.codegen.types.i8))     # argv: char**
            ]
        else:
            ll_param_tys = []

        fnty = ir.FunctionType(self.codegen.types.i32, ll_param_tys)
        c_main = ir.Function(self.codegen.module, fnty, name=symbol)
        c_main.linkage = 'external'

        if self.codegen.main_expects_args:
            c_main.args[0].name = "argc"
            c_main.args[1].name = "argv"
        return c_main

    def emit_extension_method_decl(self, ext: 'MethodDecl') -> ir.Function:
        """Create LLVM function prototype for extension method.

        The method is filed under the symbol its call site names. A method that a
        binary library ships keeps the name its bitcode gave it (`link_name`).
        """
        param_types = []
        param_names = []

        # A STATIC method has no receiver (#542): the parameter list is the arguments
        # alone.
        if ext.receiver is not None:
            self_ll = self.codegen.types.ll_type(ext.receiver)
            if ext.by_pointer:
                # `poke self` / `peek self` (#327): the receiver arrives by POINTER,
                # so a write through it reaches the caller's value. A `nom self`
                # receiver crosses BY VALUE, like the unmarked one: what it takes is
                # ownership, not an address.
                self_ll = ir.PointerType(self_ll)
            param_types.append(self_ll)
            param_names.append("self")

        for name, ty in ext.params:
            param_types.append(self.codegen.types.ll_type(ty))
            param_names.append(name)

        func_type = ir.FunctionType(declared_return_ll(self.codegen, ext), param_types)
        llvm_fn = ir.Function(self.codegen.module, func_type, name=ext.link_name)

        for i, name in enumerate(param_names):
            if i < len(llvm_fn.args):
                llvm_fn.args[i].name = name

        self.codegen.funcs.declare(ext.symbol, llvm_fn)
        return llvm_fn
