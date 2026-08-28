"""Type utility functions for LLVM IR generation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from sushi_lang.semantics.typesys import ReferenceType, Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def is_pointer_type(llvm_type: ir.Type) -> bool:
    """Check if an LLVM type is a pointer type."""
    return isinstance(llvm_type, ir.PointerType)


def is_reference_parameter(codegen: 'LLVMCodegen', var_name: str) -> bool:
    """Check if a variable is a reference parameter."""
    if var_name not in codegen.variable_types:
        return False

    var_type = codegen.variable_types[var_name]
    return isinstance(var_type, ReferenceType)


def get_semantic_type(codegen: 'LLVMCodegen', var_name: str) -> Optional[Type]:
    """Get the semantic type for a variable."""
    return codegen.variable_types.get(var_name)


def load_with_reference_handling(
    codegen: 'LLVMCodegen',
    var_name: str,
    slot: ir.AllocaInstr
) -> ir.Value:
    """Load a variable's value with automatic reference dereferencing."""
    v = codegen.builder.load(slot, name=var_name)

    if is_reference_parameter(codegen, var_name):
        # For reference parameters, we need to dereference twice:
        # 1. First load gives us the pointer (stored in the slot)
        # 2. Second load gives us the actual value (pointed to by the pointer)
        v = codegen.builder.load(v, name=f"{var_name}_deref")

    return v


def is_dynamic_array_pointer(codegen: 'LLVMCodegen', llvm_type: ir.Type) -> bool:
    """Check if an LLVM type is a pointer to a dynamic array struct."""
    if not isinstance(llvm_type, ir.PointerType):
        return False

    pointee = llvm_type.pointee
    return codegen.types.is_dynamic_array_type(pointee)


def infer_expr_semantic_type(codegen: 'LLVMCodegen', expr) -> Optional[Type]:
    """Infer the semantic type of an expression at codegen time.

    The typecheck pass's stamp is the first answer, because the backend must not re-derive a type the
    checker already decided. Everything below is reconstruction for the shapes the typecheck pass
    stamps nothing on. A shape this function cannot answer for is not merely unformatted:
    the callers read signedness off it, so `None` renders an unsigned value as SIGNED --
    `u8 255` printed `-1` through an index, a field or a `get()` (#379).
    """
    from sushi_lang.semantics.ast import (Name, IntLit, FloatLit, BinaryOp, StringLit, BoolLit,
                                          UnaryOp, CastExpr, MemberAccess, DynamicArrayFrom)
    from sushi_lang.backend.expressions.calls.utils import stamped_semantic_type
    from sushi_lang.semantics.typesys import BuiltinType

    stamped = stamped_semantic_type(codegen, expr)
    if stamped is not None:
        return stamped

    # Variable: look up in scope manager (supports nested scopes). Constants are
    # not registered in the scope tables, so fall back to the constant table -
    # otherwise a `const u32` reference loses its type and formats as signed.
    if isinstance(expr, Name):
        local_type = codegen.memory.find_semantic_type(expr.id)
        if local_type is not None:
            return local_type
        const_table = getattr(codegen, 'const_table', None)
        if const_table is not None:
            sig = const_table.lookup(expr.id, codegen.emitting_unit, codegen.scope)
            if sig is not None and sig.const_type is not None:
                return sig.const_type
        return None

    elif isinstance(expr, MemberAccess):
        return _field_semantic_type(codegen, expr)

    elif isinstance(expr, CastExpr):
        return expr.target_type

    elif isinstance(expr, IntLit):
        # The context type the typecheck pass stamped, else the default. A literal in an `i64[]`
        # element position is i64, and answering i32 for it would report the array's
        # element type wrongly to every caller that asks.
        return expr.resolved_type or BuiltinType.I32

    elif isinstance(expr, FloatLit):
        return expr.resolved_type or BuiltinType.F64

    elif isinstance(expr, DynamicArrayFrom):
        return _dynamic_array_from_type(codegen, expr)

    elif isinstance(expr, StringLit):
        return BuiltinType.STRING

    elif isinstance(expr, BoolLit):
        return BuiltinType.BOOL

    elif isinstance(expr, UnaryOp):
        return infer_expr_semantic_type(codegen, expr.expr)

    elif isinstance(expr, BinaryOp):
        if expr.op in ["&", "|", "^", "<<", ">>"]:
            return infer_expr_semantic_type(codegen, expr.left)

        # Arithmetic operators: strict same-type rule (mirrors the typecheck pass) -
        # the result is the common operand type; trust the known side when
        # only one can be reconstructed here.
        elif expr.op in ["+", "-", "*", "/", "%"]:
            left_type = infer_expr_semantic_type(codegen, expr.left)
            right_type = infer_expr_semantic_type(codegen, expr.right)

            if left_type is not None:
                return left_type
            if right_type is not None:
                return right_type

            return BuiltinType.I32

        elif expr.op in ["==", "!=", "<", "<=", ">", ">="]:
            return BuiltinType.BOOL

        elif expr.op in ["and", "or", "xor", "&&", "||", "^^"]:
            return BuiltinType.BOOL

    # Cannot infer type for other expression types
    return None


def _dynamic_array_from_type(codegen: 'LLVMCodegen', expr) -> Optional[Type]:
    """The `T[]` an inline `from([...])` produces, read off its first element."""
    from sushi_lang.semantics.typesys import DynamicArrayType

    elements = expr.elements.elements
    if not elements:
        return None
    element_type = infer_expr_semantic_type(codegen, elements[0].value)
    return None if element_type is None else DynamicArrayType(base_type=element_type)


def _field_semantic_type(codegen: 'LLVMCodegen', expr) -> Optional[Type]:
    """The declared type of the field a member access reads, or None."""
    from sushi_lang.backend.expressions.structs import infer_struct_type
    from sushi_lang.internals.diagnostics import InternalCompilerError
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    from sushi_lang.semantics.typesys import UnknownType

    try:
        owner = infer_struct_type(codegen, expr.receiver)
    except InternalCompilerError:
        # A receiver that names no struct -- an FFI namespace, an enum type name. Those
        # shapes reach this function too, and none of them has a field to read.
        return None

    field_type = owner.get_field_type(expr.member)
    if field_type is None:
        return None

    resolved = resolve_unknown_type(field_type, codegen.struct_table.by_name,
                                    codegen.enum_table.by_name)
    return None if isinstance(resolved, UnknownType) else resolved


def is_unsigned_type(semantic_type: Optional[Type]) -> bool:
    """Check whether a semantic type is an unsigned integer type."""
    from sushi_lang.semantics.type_predicates import is_unsigned_int
    return is_unsigned_int(semantic_type)
