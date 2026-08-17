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
    # Load the value from the slot
    v = codegen.builder.load(slot, name=var_name)

    # Check if this is a reference parameter
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
    """Infer the semantic type of an expression at codegen time."""
    from sushi_lang.semantics.ast import Name, IntLit, FloatLit, BinaryOp, StringLit, BoolLit, UnaryOp, CastExpr
    from sushi_lang.semantics.typesys import BuiltinType

    # Variable: look up in scope manager (supports nested scopes). Constants are
    # not registered in the scope tables, so fall back to the constant table -
    # otherwise a `const u32` reference loses its type and formats as signed.
    if isinstance(expr, Name):
        local_type = codegen.memory.find_semantic_type(expr.id)
        if local_type is not None:
            return local_type
        const_table = getattr(codegen, 'const_table', None)
        if const_table is not None:
            sig = const_table.by_name.get(expr.id)
            if sig is not None and sig.const_type is not None:
                return sig.const_type
        return None

    # Explicit cast: the target type IS the semantic type
    elif isinstance(expr, CastExpr):
        return expr.target_type

    # Integer literals default to i32
    elif isinstance(expr, IntLit):
        return BuiltinType.I32

    # Float literals default to f64
    elif isinstance(expr, FloatLit):
        return BuiltinType.F64

    # String literals
    elif isinstance(expr, StringLit):
        return BuiltinType.STRING

    # Boolean literals
    elif isinstance(expr, BoolLit):
        return BuiltinType.BOOL

    # Unary operations preserve operand type
    elif isinstance(expr, UnaryOp):
        return infer_expr_semantic_type(codegen, expr.expr)

    # Binary operations: type inference rules
    elif isinstance(expr, BinaryOp):
        # Bitwise operators return the type of the left operand
        if expr.op in ["&", "|", "^", "<<", ">>"]:
            return infer_expr_semantic_type(codegen, expr.left)

        # Arithmetic operators: strict same-type rule (mirrors Pass 2) -
        # the result is the common operand type; trust the known side when
        # only one can be reconstructed here.
        elif expr.op in ["+", "-", "*", "/", "%"]:
            left_type = infer_expr_semantic_type(codegen, expr.left)
            right_type = infer_expr_semantic_type(codegen, expr.right)

            if left_type is not None:
                return left_type
            if right_type is not None:
                return right_type

            # Default fallback for integers
            return BuiltinType.I32

        # Comparison operators return bool
        elif expr.op in ["==", "!=", "<", "<=", ">", ">="]:
            return BuiltinType.BOOL

        # Logical operators return bool
        elif expr.op in ["and", "or", "xor", "&&", "||", "^^"]:
            return BuiltinType.BOOL

    # Cannot infer type for other expression types
    return None


def is_unsigned_type(semantic_type: Optional[Type]) -> bool:
    """Check whether a semantic type is an unsigned integer type."""
    from sushi_lang.semantics.type_predicates import is_unsigned_int
    return is_unsigned_int(semantic_type)
