"""Function declaration handling for LLVM code generation."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import FuncDef, ExtendDef
from sushi_lang.semantics.unit_symbols import mangle_unit_symbol
from sushi_lang.backend.functions.helpers import declared_result_of

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def declaring_unit(fn: FuncDef, unit_name: str | None) -> str | None:
    """Whose symbol this declaration takes.

    A monomorphized instance goes home to the unit that declared its generic (#495):
    `home_unit` is stamped by `generics/synthesis.py`, and two units' instances of
    one mangled base name become two symbols. A lifted lambda carries no home and
    keeps its bare name -- the per-unit lifter's counter already makes it unique
    (#402) -- and so does a binary library's template instance, whose home names no
    unit in the build.
    """
    if getattr(fn, "is_synthesized", False):
        return getattr(fn, "home_unit", None)
    return unit_name


class FunctionDeclarations:
    """Handles LLVM function prototype generation."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize declarations handler with reference to main codegen instance."""
        self.codegen = codegen

    def emit_func_decl(self, fn: FuncDef, unit_name: str | None = None) -> ir.Function:
        """Create LLVM function prototype for regular function.

        `unit_name` is the unit whose declaration this is, and it decides the LLVM
        symbol: two units may each declare a private `helper`, and the monolithic path
        puts both into one module. A synthesized body -- a monomorphized instance, a
        lifted lambda -- belongs to no unit and keeps the bare name it was given.
        """
        unit_name = declaring_unit(fn, unit_name)
        existing = self.codegen.funcs.declared(fn.name, unit_name)
        if existing is not None:
            return existing
        symbol = mangle_unit_symbol(unit_name, fn.name)

        # Special handling for main function - it needs C-compatible signature
        # Main always needs a wrapper because Sushi functions return Result<T>
        # but C expects int main()
        # Skip wrapper in library mode (main is just a regular function)
        if fn.name == 'main' and not getattr(self.codegen, 'is_library_mode', False):
            if self.codegen.main_expects_args:
                ll_param_tys = [
                    self.codegen.types.i32,                                    # argc: int
                    ir.PointerType(ir.PointerType(self.codegen.types.i8))     # argv: char**
                ]
            else:
                ll_param_tys = []

            ll_ret = self.codegen.types.i32  # main always returns int in C
            fnty = ir.FunctionType(ll_ret, ll_param_tys)
            llvm_fn = ir.Function(self.codegen.module, fnty, name=symbol)

            if self.codegen.main_expects_args:
                llvm_fn.args[0].name = "argc"
                llvm_fn.args[1].name = "argv"
        else:
            params = self.codegen.functions.helpers.params_of(fn)
            ll_param_tys = [self.codegen.types.ll_type(ty) for _, ty in params]
            ll_ret = self.codegen.types.ll_type(declared_result_of(self.codegen, fn))

            fnty = ir.FunctionType(ll_ret, ll_param_tys)
            llvm_fn = ir.Function(self.codegen.module, fnty, name=symbol)

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

        self.codegen.funcs.declare(fn.name, llvm_fn, unit=unit_name)

        if fn.name != 'main' and fn.ret is not None:
            self.codegen.function_return_types.declare(
                fn.name, declared_result_of(self.codegen, fn), unit=unit_name)

        return llvm_fn

    def emit_extension_method_decl(self, ext: ExtendDef) -> ir.Function:
        """Create LLVM function prototype for extension method."""
        func_name = self.codegen.functions.helpers.get_extension_method_name(ext)

        param_types = []
        param_names = []

        # A STATIC method has no receiver (#542): the parameter list is the arguments
        # alone, and `receiver_mode` must never be reached for one.
        if ext.target_type and not getattr(ext, "is_static", False):
            from sushi_lang.semantics.param_modes import receiver_mode
            self_ll = self.codegen.types.ll_type(ext.target_type)
            if receiver_mode(getattr(ext, "self_mode", None)).by_pointer:
                # `poke self` / `peek self` (#327): the receiver arrives by POINTER,
                # so a write through it reaches the caller's value. A `nom self`
                # receiver crosses BY VALUE, like the unmarked one: what it takes is
                # ownership, not an address.
                self_ll = ir.PointerType(self_ll)
            param_types.append(self_ll)
            param_names.append("self")

        for param in ext.params:
            if param.ty:
                param_types.append(self.codegen.types.ll_type(param.ty))
                param_names.append(param.name)

        # A channel extension ('| E', ruling 1) has the Result ABI; a bare one keeps
        # the unwrapped return.
        from sushi_lang.backend.generics.result_builder import extension_result_of
        channel = extension_result_of(self.codegen, ext)
        if channel is not None:
            ret_type = self.codegen.types.ll_type(channel)
        elif ext.ret:
            ret_type = self.codegen.types.ll_type(ext.ret)
        else:
            ret_type = ir.VoidType()

        func_type = ir.FunctionType(ret_type, param_types)
        llvm_fn = ir.Function(self.codegen.module, func_type, name=func_name)

        for i, name in enumerate(param_names):
            if i < len(llvm_fn.args):
                llvm_fn.args[i].name = name

        self.codegen.funcs.declare(func_name, llvm_fn)
        return llvm_fn
