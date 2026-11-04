"""Helper functions for LLVM function management."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Tuple

from llvmlite import ir
from sushi_lang.semantics.ast import FuncDef, Param, ExtendDef
from sushi_lang.semantics.typesys import Type as Ty
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class FunctionHelpers:
    """Utility functions for function emission."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize helpers with reference to main codegen instance."""
        self.codegen = codegen

    def params_of(self, fn: FuncDef) -> List[Tuple[str, Ty]]:
        """Extract parameter information from function definition."""
        out: List[Tuple[str, Ty]] = []
        for idx, p in enumerate(getattr(fn, "params", ())):
            if not isinstance(p, Param):
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] must be Param, got {type(p).__name__}")

            if not isinstance(p.name, str):
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] name must be str, got {type(p.name).__name__}")

            if p.ty is None:
                raise_internal_error("CE0015", message=f"{fn.name}: param[{idx}] '{p.name}' has no type")

            out.append((p.name, p.ty))
        return out

    def get_extension_method_name(self, ext: ExtendDef, unit_name: str | None = None) -> str:
        """The symbol of an extension method, as its call site names it
        (`extension_decl_symbol`, the one derivation that SLIR lowering reads too)."""
        from sushi_lang.semantics.generics.name_mangling import extension_decl_symbol
        return extension_decl_symbol(ext, unit_name)


def declare_stdlib_function(
    module: ir.Module,
    func_name: str,
    return_type: ir.Type,
    param_types: list[ir.Type]
) -> ir.Function:
    """Declare an external stdlib function."""
    if func_name in module.globals:
        existing = module.globals[func_name]
        if isinstance(existing, ir.Function):
            return existing

    fn_type = ir.FunctionType(return_type, param_types)
    func = ir.Function(module, fn_type, name=func_name)
    return func
