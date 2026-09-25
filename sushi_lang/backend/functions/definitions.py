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

    def emit_func_def(self, fn: FuncDef, unit_name: str | None = None) -> ir.Function:
        """Define the body of a regular function."""
        if fn.name == 'main' and not getattr(self.codegen, 'is_library_mode', False):
            return self.codegen.functions.main_wrapper.emit_main(fn)

        from sushi_lang.backend.functions.declarations import declaring_unit
        llvm_fn = (self.codegen.funcs.declared(fn.name, declaring_unit(fn, unit_name))
                   or self.codegen.functions.declarations.emit_func_decl(fn, unit_name))
        self.emit_body(llvm_fn, fn)
        return llvm_fn

    def emit_body(self, llvm_fn: ir.Function, fn: FuncDef) -> None:
        """Emit the body of `fn` into `llvm_fn`."""
        helpers = self.codegen.functions.helpers
        helpers.begin_function(llvm_fn, fn)

        self.codegen.current_function_ast = fn

        for param in fn.params:
            if param.ty is not None:
                self.codegen.variable_types[param.name] = param.ty

        self.codegen.statements.emit_block(fn.body)

        if self.codegen.builder.block.terminator is None:
            helpers.emit_default_return(fn)

        helpers.end_function()

        self.codegen.current_function_ast = None

    def emit_extension_method_def(self, ext: ExtendDef) -> ir.Function:
        """Define the body of an extension method."""
        helpers = self.codegen.functions.helpers
        func_name = helpers.get_extension_method_name(ext)
        llvm_fn = self.codegen.funcs.get(func_name)
        if not llvm_fn:
            raise_internal_error("CE0025", name=func_name)

        # A method's parameters obey the same modes as any callable's -- a `nom` one is
        # OWNED by the body and leaks unless registered (borrow-model.md S1).
        helpers.begin_function(llvm_fn, ext)

        self.codegen.in_extension_method = True
        # A channel body ('| E') spells both constructors (#848); only the fall-off path,
        # which CE0107 keeps unreachable, reads the channel here.
        from sushi_lang.backend.generics.result_builder import extension_result_of
        self.codegen.current_extension_result = extension_result_of(self.codegen, ext)

        # Track 'self' and parameter types in variable_types for struct member access
        # resolution. A moded receiver (#327) registers its full ReferenceType -- the
        # single fact `is_reference_parameter` keys on, so every deref/write consumer
        # treats `self` as the pointer it now is.
        from sushi_lang.semantics.param_modes import receiver_mode
        self_receiver_mode = receiver_mode(getattr(ext, "self_mode", None))
        # A static has no receiver to register (#542). CE0134 already refused a body
        # that names one, so nothing here reads `self`.
        self_semantic = None if getattr(ext, "is_static", False) else ext.target_type
        if self_semantic is not None and self_receiver_mode.by_pointer:
            from sushi_lang.semantics.typesys import ReferenceType
            self_semantic = ReferenceType(ext.target_type, self_receiver_mode.borrow_mode)
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

        # A `nom self` receiver is OWNED by this body, exactly as a `nom` parameter is,
        # so it needs the registration that frees it at scope exit. Every other receiver
        # mode is a borrow and must stay unregistered, or the body frees the caller's
        # value (ruling R25).
        if self_receiver_mode.consumes and self_semantic is not None:
            slot = self.codegen.memory.try_find_local_slot("self")
            if slot is not None:
                self.codegen.memory.register_owning_value(
                    "self", ext.target_type, slot)

        self.codegen.statements.emit_block(ext.body)

        if self.codegen.builder.block.terminator is None:
            channel = self.codegen.current_extension_result
            if channel is not None:
                from sushi_lang.backend.generics.result_builder import build_err_from_return_type
                from sushi_lang.backend.statements import utils
                utils.emit_scope_cleanup(self.codegen)
                self.codegen.builder.ret(
                    build_err_from_return_type(self.codegen, channel, None))
            else:
                helpers.emit_default_return_for_extension(ext.ret)

        self.codegen.in_extension_method = False
        self.codegen.current_extension_result = None
        helpers.end_function()
        return llvm_fn
