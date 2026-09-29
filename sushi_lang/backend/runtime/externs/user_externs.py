"""Declaration of user-declared foreign functions (FFI `unsafe external` blocks)."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.semantics.ffi_boundary import is_byte_buffer, nullable_payload
from sushi_lang.semantics.typesys import BuiltinType

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.passes.collect.externals import ExternalSig, ExternalTable


def _abi_param_type(codegen: 'LLVMCodegen', ty) -> ir.Type:
    """Lower a parameter type to its C-ABI LLVM type."""
    if (ty == BuiltinType.STRING or nullable_payload(ty) is not None
            or is_byte_buffer(ty)):
        return ir.PointerType(codegen.i8)
    return codegen.types.ll_type(ty)


def _abi_return_type(codegen: 'LLVMCodegen', ty) -> ir.Type:
    """Lower a return type to its C-ABI LLVM type."""
    if ty is None:
        return ir.VoidType()
    if isinstance(ty, BuiltinType) and ty == BuiltinType.BLANK:
        return ir.VoidType()
    if ty == BuiltinType.STRING or nullable_payload(ty) is not None:
        return ir.PointerType(codegen.i8)
    return codegen.types.ll_type(ty)


def declare_user_externs(codegen: 'LLVMCodegen', external_table: 'ExternalTable') -> None:
    """Declare every foreign function in the external table."""
    if not hasattr(codegen, 'external_funcs'):
        codegen.external_funcs = {}
    if not hasattr(codegen, 'external_sigs'):
        codegen.external_sigs = {}

    if external_table is None:
        return

    for namespace, decls in external_table.by_namespace.items():
        for name, sig in decls.items():
            llvm_fn = _declare_one(codegen, sig)
            codegen.external_funcs[(namespace, name)] = llvm_fn
            codegen.external_sigs[(namespace, name)] = sig

    codegen.external_vars = {}
    for namespace, variables in external_table.variables.items():
        for name, var in variables.items():
            codegen.external_vars[(namespace, name)] = (_declare_variable(codegen, var), var)


def _declare_variable(codegen: 'LLVMCodegen', var) -> ir.GlobalVariable:
    """Declare (or reuse) a C global variable (#1090): no initializer, so `external`."""
    existing = codegen.module.globals.get(var.link_name)
    if isinstance(existing, ir.GlobalVariable):
        return existing
    return ir.GlobalVariable(codegen.module, _abi_return_type(codegen, var.ty),
                             name=var.link_name)


def _declare_one(codegen: 'LLVMCodegen', sig: 'ExternalSig') -> ir.Function:
    """Declare (or reuse) the LLVM function for a single foreign signature."""
    existing = codegen.module.globals.get(sig.link_name)
    if isinstance(existing, ir.Function):
        return existing

    ret_ll = _abi_return_type(codegen, sig.ret_type)
    param_lls = [_abi_param_type(codegen, ty) for ty in sig.param_types]
    fn_ty = ir.FunctionType(ret_ll, param_lls, var_arg=getattr(sig, "is_variadic", False))
    return ir.Function(codegen.module, fn_ty, name=sig.link_name)
