"""
Expression emission module for the Sushi language compiler.

This module provides a refactored, modular approach to LLVM IR generation
for expressions. The code is organized by expression category for better
maintainability and clarity.

Main entry point: ExpressionEmitter.emit_expr()
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.semantics.ast import Expr
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class ExpressionEmitter:
    """Main expression emitter that delegates to specialized submodules."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize expression emitter with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context and builders.
        """
        self.codegen = codegen

    def emit_expr(self, expr: Expr, to_i1: bool = False) -> 'ir.Value':
        """Emit LLVM IR for an expression and return its SSA value.

        This is the main entry point for expression emission. It delegates
        to specialized emitters based on the expression type.

        Args:
            expr: The expression AST node to emit.
            to_i1: Whether to convert the result to i1 for boolean contexts.

        Returns:
            The LLVM value representing the expression result.

        Raises:
            NotImplementedError: If the expression type is not supported.
        """
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        self.codegen.utils.ensure_open_block()

        # Import specialized emitters
        from sushi_lang.semantics.ast import (
            IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString,
            ArrayLiteral, IndexAccess, UnaryOp, BinaryOp, Name, Call, MethodCall,
            MemberAccess, DynamicArrayNew, DynamicArrayFrom, CastExpr, Borrow,
            EnumConstructor, DotCall, TryExpr
        )

        # Delegate to appropriate specialized emitter based on expression type
        match expr:
            # Literals
            case IntLit() | FloatLit() | BoolLit() | BlankLit() | StringLit() | InterpolatedString():
                from sushi_lang.backend.expressions import literals
                return literals.emit_literal(self.codegen, expr, to_i1)

            # Operators and names
            case UnaryOp() | BinaryOp():
                from sushi_lang.backend.expressions import operators
                return operators.emit_operator(self.codegen, expr, to_i1)

            case Name():
                from sushi_lang.backend.expressions import operators
                return operators.emit_name(self.codegen, expr, to_i1)

            case Borrow():
                from sushi_lang.backend.expressions import operators
                return operators.emit_borrow(self.codegen, expr)

            case TryExpr():
                from sushi_lang.backend.expressions import operators
                return operators.emit_try_expr(self.codegen, expr)

            # Arrays
            case ArrayLiteral():
                from sushi_lang.backend.types import arrays
                return arrays.emit_array_literal(self.codegen, expr)

            case IndexAccess():
                from sushi_lang.backend.types import arrays
                return arrays.emit_index_access(self.codegen, expr, to_i1)

            case DynamicArrayNew():
                from sushi_lang.backend.types import arrays
                return arrays.emit_dynamic_array_new(self.codegen, expr)

            case DynamicArrayFrom():
                from sushi_lang.backend.types import arrays
                return arrays.emit_dynamic_array_from(self.codegen, expr)

            # Function and method calls
            case Call():
                from sushi_lang.backend.expressions import calls
                return calls.emit_function_call(self.codegen, expr, to_i1)

            case MethodCall():
                from sushi_lang.backend.expressions import calls
                return calls.emit_method_call(self.codegen, expr, to_i1)

            case DotCall():
                # Resolve DotCall to either enum constructor or method call
                if isinstance(expr.receiver, Name):
                    receiver_name = expr.receiver.id
                    # Check if it's an enum type (concrete or generic)
                    if receiver_name in self.codegen.enum_table.by_name:
                        from sushi_lang.backend.expressions import enums
                        return enums.emit_enum_constructor(self.codegen, expr, is_dotcall=True)
                    # Check for resolved generic enum type (e.g., Result<T>)
                    elif hasattr(expr, 'resolved_enum_type') and expr.resolved_enum_type is not None:
                        # CRITICAL: Verify method is actually a variant, not a method like realise()
                        from sushi_lang.semantics.typesys import EnumType
                        resolved_type = expr.resolved_enum_type
                        if isinstance(resolved_type, EnumType) and resolved_type.get_variant(expr.method) is not None:
                            from sushi_lang.backend.expressions import enums
                            return enums.emit_enum_constructor(self.codegen, expr, is_dotcall=True)
                        # Not a variant - fall through to method call dispatch
                # Otherwise, it's a method call
                from sushi_lang.backend.expressions import calls
                return calls.emit_method_call(self.codegen, expr, to_i1, is_dotcall=True)

            # Structs and enum variants (zero-argument)
            case MemberAccess():
                # Check if this is enum variant access (EnumType.Variant)
                if isinstance(expr.receiver, Name):
                    receiver_name = expr.receiver.id
                    if receiver_name in self.codegen.enum_table.by_name:
                        # This is an enum variant, not struct field
                        from sushi_lang.backend.expressions import enums
                        enum_type = self.codegen.enum_table.by_name[receiver_name]
                        return enums.emit_enum_constructor_from_method_call(
                            self.codegen, enum_type, expr.member, []
                        )

                # Regular struct member access
                from sushi_lang.backend.expressions import structs
                return structs.emit_member_access(self.codegen, expr, to_i1)

            # Enums
            case EnumConstructor():
                from sushi_lang.backend.expressions import enums
                return enums.emit_enum_constructor(self.codegen, expr)

            # Type casting
            case CastExpr():
                from sushi_lang.backend.expressions import casts
                return casts.emit_cast_expression(self.codegen, expr)

            case _:
                raise NotImplementedError(f"Expression type not supported: {type(expr).__name__}")


__all__ = ['ExpressionEmitter']
