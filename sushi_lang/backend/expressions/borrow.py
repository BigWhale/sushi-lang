"""Borrow (peek / poke) emission for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import Borrow
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_borrow(codegen: 'LLVMCodegen', expr: Borrow) -> ir.Value:
    """Emit borrow expression (peek expr or poke expr) as pointer to expression."""
    from sushi_lang.semantics.ast import Name, MemberAccess
    from sushi_lang.semantics.typesys import ReferenceType

    if isinstance(expr.expr, Name):
        # Original logic: borrow a variable. Only a local is borrowable, and
        # find_local_slot raises CE0055 itself if the name is not one -- which is exactly
        # what the `except KeyError` here used to re-raise by hand.
        var_name = expr.expr.id
        slot = codegen.memory.find_local_slot(var_name)

        if hasattr(codegen, 'variable_types') and var_name in codegen.variable_types:
            semantic_type = codegen.variable_types[var_name]
            if isinstance(semantic_type, ReferenceType):
                return codegen.builder.load(slot, name=f"{var_name}_ref_ptr")

        return slot  # Return the pointer directly (zero-cost)

    elif isinstance(expr.expr, MemberAccess):
        return emit_member_access_borrow(codegen, expr.expr)

    else:
        # Should never reach here (borrow checker validates this)
        raise_internal_error("CE0100", expr=type(expr.expr).__name__)


def emit_member_access_borrow(codegen: 'LLVMCodegen', expr) -> ir.Value:
    """Emit borrow of struct field access using GEP."""
    from sushi_lang.backend.expressions.structs import infer_struct_type, try_get_struct_alloca

    struct_type = infer_struct_type(codegen, expr.receiver)
    field_index = struct_type.get_field_index(expr.member)

    if field_index is None:
        raise_internal_error("CE0029", struct=struct_type.name, field=expr.member)

    struct_ptr = try_get_struct_alloca(codegen, expr.receiver)

    if struct_ptr is None:
        raise_internal_error("CE0030")

    zero = ir.Constant(codegen.types.i32, 0)
    field_idx = ir.Constant(codegen.types.i32, field_index)
    field_ptr = codegen.builder.gep(
        struct_ptr,
        [zero, field_idx],
        name=f"{expr.member}_ptr"
    )

    return field_ptr

