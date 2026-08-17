"""Function definition (body emission) for LLVM code generation."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import FuncDef, ExtendDef
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class FunctionDefinitions:
    """Handles LLVM function body emission."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize definitions handler with reference to main codegen instance."""
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
        """Define the body of a regular function."""
        if fn.name == 'main' and not getattr(self.codegen, 'is_library_mode', False):
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

        llvm_fn = self.codegen.funcs.get(fn.name) or emit_func_decl_fn(fn)
        begin_function_fn(llvm_fn, fn)

        self.codegen.current_function_ast = fn

        for param in fn.params:
            if param.ty is not None:
                self.codegen.variable_types[param.name] = param.ty

        self.codegen.statements.emit_block(fn.body)

        if self.codegen.builder.block.terminator is None:
            emit_default_return_fn(fn.ret)

        end_function_fn()

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
        """Define the body of an extension method."""
        func_name = get_name_fn(ext)
        llvm_fn = self.codegen.funcs.get(func_name)
        if not llvm_fn:
            raise_internal_error("CE0025", name=func_name)

        # An `ExtendDef` is no `FuncDef`, but `begin_function` reads only `.params`, and a
        # method's parameters obey the same modes as any callable's -- a `nom` one is OWNED
        # by the body and leaks unless registered. Passing None was the old proxy for "is
        # this a method body?", which the declared mode now answers (borrow-model.md S1).
        begin_function_fn(llvm_fn, ext)

        self.codegen.in_extension_method = True

        # Track 'self' and parameter types in variable_types for struct member access
        # resolution. A moded receiver (#327) registers its full ReferenceType -- the
        # single fact `is_reference_parameter` keys on, so every deref/write consumer
        # treats `self` as the pointer it now is.
        self_semantic = ext.target_type
        if self_semantic is not None and getattr(ext, "self_mode", None) is not None:
            from sushi_lang.semantics.typesys import BorrowMode, ReferenceType
            self_semantic = ReferenceType(
                ext.target_type,
                BorrowMode.POKE if ext.self_mode == "poke" else BorrowMode.PEEK)
        if self_semantic is not None:
            self.codegen.variable_types["self"] = self_semantic
        for param in ext.params:
            if param.ty is not None:
                self.codegen.variable_types[param.name] = param.ty

        # Register semantic types for 'self' and params in the memory manager so
        # receiver dispatch (e.g. `self.iter()` on a List<T> receiver) can recognise
        # them. Extension bodies begin_function with fn_def=None, so begin_function
        # never records these. set_semantic_type does NOT register for RAII cleanup,
        # keeping the by-value `self` unfreed (no double-free of a shared buffer).
        if self_semantic is not None:
            self.codegen.memory.set_semantic_type("self", self_semantic)
        for param in ext.params:
            if param.ty is not None:
                self.codegen.memory.set_semantic_type(param.name, param.ty)

        self.codegen.statements.emit_block(ext.body)

        if self.codegen.builder.block.terminator is None:
            emit_default_return_for_extension_fn(ext.ret)

        self.codegen.in_extension_method = False
        end_function_fn()
        return llvm_fn
