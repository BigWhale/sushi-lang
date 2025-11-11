"""
Type utility functions for LLVM IR generation.

This module provides helper functions for type checking, semantic type lookup,
and common type-related operations used throughout the expression emission code.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from llvmlite import ir
from semantics.typesys import ReferenceType, Type

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


def is_pointer_type(llvm_type: ir.Type) -> bool:
    """Check if an LLVM type is a pointer type.

    Args:
        llvm_type: The LLVM type to check.

    Returns:
        True if the type is a pointer type, False otherwise.
    """
    return isinstance(llvm_type, ir.PointerType)


def is_reference_parameter(codegen: 'LLVMCodegen', var_name: str) -> bool:
    """Check if a variable is a reference parameter.

    Reference parameters are tracked in the semantic type system with ReferenceType.
    This function checks if the given variable has a reference type.

    Args:
        codegen: The LLVM code generator.
        var_name: The name of the variable to check.

    Returns:
        True if the variable is a reference parameter, False otherwise.
    """
    if var_name not in codegen.variable_types:
        return False

    var_type = codegen.variable_types[var_name]
    return isinstance(var_type, ReferenceType)


def get_semantic_type(codegen: 'LLVMCodegen', var_name: str) -> Optional[Type]:
    """Get the semantic type for a variable.

    This function looks up the semantic type from the variable_types table.
    Returns None if the variable is not found or has no type information.

    Args:
        codegen: The LLVM code generator.
        var_name: The name of the variable.

    Returns:
        The semantic type of the variable, or None if not found.
    """
    return codegen.variable_types.get(var_name)


def load_with_reference_handling(
    codegen: 'LLVMCodegen',
    var_name: str,
    slot: ir.AllocaInstr
) -> ir.Value:
    """Load a variable's value with automatic reference dereferencing.

    For regular variables: Loads the value from the alloca slot.
    For reference parameters: Loads the pointer, then dereferences it to get the actual value.

    References are zero-cost abstractions in Sushi. The borrow checker (Pass 3)
    has already verified that all reference usage is safe.

    Args:
        codegen: The LLVM code generator.
        var_name: The name of the variable to load.
        slot: The alloca instruction (pointer to variable's memory).

    Returns:
        The loaded value (automatically dereferenced for references).
    """
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
    """Check if an LLVM type is a pointer to a dynamic array struct.

    Dynamic arrays in Sushi are represented as structs with {i8*, i32, i32} layout
    (data pointer, length, capacity). This function checks if a type is a pointer
    to such a struct.

    Args:
        codegen: The LLVM code generator.
        llvm_type: The LLVM type to check.

    Returns:
        True if the type is a pointer to a dynamic array struct, False otherwise.
    """
    if not isinstance(llvm_type, ir.PointerType):
        return False

    pointee = llvm_type.pointee
    return codegen.types.is_dynamic_array_type(pointee)


def infer_expr_semantic_type(codegen: 'LLVMCodegen', expr) -> Optional[Type]:
    """Infer the semantic type of an expression at codegen time.

    This function mirrors the type inference logic from semantic analysis Pass 2,
    allowing codegen to determine expression types for operations that need to
    distinguish signed/unsigned operands (like right shift).

    This is necessary because type information from semantic analysis is not
    stored on AST nodes, so it must be reconstructed at codegen time.

    Args:
        codegen: The LLVM code generator.
        expr: The expression AST node to infer the type for.

    Returns:
        The semantic type of the expression, or None if it cannot be inferred.

    Examples:
        >>> infer_expr_semantic_type(codegen, Name("x"))  # Variable lookup
        BuiltinType.U32
        >>> infer_expr_semantic_type(codegen, BinaryOp("+", x, IntLit(1)))  # Complex expr
        BuiltinType.U32
    """
    from semantics.ast import Name, IntLit, FloatLit, BinaryOp, StringLit, BoolLit, UnaryOp
    from semantics.typesys import BuiltinType

    # Variable: look up in scope manager (supports nested scopes)
    if isinstance(expr, Name):
        return codegen.memory.find_semantic_type(expr.id)

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

        # Arithmetic operators: infer from both operands
        elif expr.op in ["+", "-", "*", "/", "%"]:
            left_type = infer_expr_semantic_type(codegen, expr.left)
            right_type = infer_expr_semantic_type(codegen, expr.right)

            # Float promotion: if either operand is float, result is float
            if left_type in [BuiltinType.F32, BuiltinType.F64]:
                return BuiltinType.F64
            elif right_type in [BuiltinType.F32, BuiltinType.F64]:
                return BuiltinType.F64

            # Integer arithmetic: preserve left operand type if both are integers
            if left_type and right_type:
                return left_type

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
