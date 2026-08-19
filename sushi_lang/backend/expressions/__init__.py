"""Expression emission module for the Sushi language compiler."""
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
        """Initialize expression emitter with reference to main codegen instance."""
        self.codegen = codegen

    def emit_expr(self, expr: Expr, to_i1: bool = False) -> 'ir.Value':
        """Emit LLVM IR for an expression and return its SSA value."""
        if self.codegen.builder is None:
            raise_internal_error("CE0009")
        self.codegen.utils.ensure_open_block()

        from sushi_lang.semantics.ast import (
            IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString,
            ArrayLiteral, IndexAccess, UnaryOp, BinaryOp, Name, Call, MethodCall,
            MemberAccess, DynamicArrayNew, DynamicArrayFrom, CastExpr, Borrow,
            EnumConstructor, DotCall, TryExpr, Lambda
        )

        match expr:
            case IntLit() | FloatLit() | BoolLit() | BlankLit() | StringLit() | InterpolatedString():
                from sushi_lang.backend.expressions import literals
                return literals.emit_literal(self.codegen, expr, to_i1)

            case UnaryOp() | BinaryOp():
                from sushi_lang.backend.expressions import operators
                return operators.emit_operator(self.codegen, expr, to_i1)

            case Name():
                from sushi_lang.backend.expressions import names
                return names.emit_name(self.codegen, expr, to_i1)

            case Borrow():
                from sushi_lang.backend.expressions import borrow
                return borrow.emit_borrow(self.codegen, expr)

            case TryExpr():
                from sushi_lang.backend.expressions import try_expr
                return try_expr.emit_try_expr(self.codegen, expr)

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

            case Call():
                from sushi_lang.backend.expressions import calls
                return calls.emit_function_call(self.codegen, expr, to_i1)

            case MethodCall():
                from sushi_lang.backend.expressions import calls
                return calls.emit_method_call(self.codegen, expr, to_i1)

            case DotCall():
                _fn_field_ty = getattr(expr, 'callee_fn_type', None)
                if _fn_field_ty is not None:
                    from sushi_lang.semantics.typesys import FunctionType
                    if isinstance(_fn_field_ty, FunctionType):
                        from sushi_lang.backend.expressions import calls
                        return calls.emit_fn_field_call(self.codegen, expr, _fn_field_ty, to_i1)
                if isinstance(expr.receiver, Name):
                    receiver_name = expr.receiver.id
                    # Local-wins (#296): a local named after an enum shadows it.
                    if (receiver_name in self.codegen.enum_table.by_name
                            and self.codegen.memory.find_semantic_type(receiver_name) is None):
                        from sushi_lang.backend.expressions import enums
                        return enums.emit_enum_constructor(self.codegen, expr, is_dotcall=True)
                    elif hasattr(expr, 'resolved_enum_type') and expr.resolved_enum_type is not None:
                        from sushi_lang.semantics.typesys import EnumType
                        resolved_type = expr.resolved_enum_type
                        if isinstance(resolved_type, EnumType) and resolved_type.get_variant(expr.method) is not None:
                            from sushi_lang.backend.expressions import enums
                            return enums.emit_enum_constructor(self.codegen, expr, is_dotcall=True)
                from sushi_lang.backend.expressions import calls
                return calls.emit_method_call(self.codegen, expr, to_i1, is_dotcall=True)

            case MemberAccess():
                if isinstance(expr.receiver, Name):
                    receiver_name = expr.receiver.id
                    # Local-wins (#296): `Color.v` on a local named Color is a field read.
                    if (receiver_name in self.codegen.enum_table.by_name
                            and self.codegen.memory.find_semantic_type(receiver_name) is None):
                        from sushi_lang.backend.expressions import enums
                        enum_type = self.codegen.enum_table.by_name[receiver_name]
                        return enums.emit_enum_constructor_from_method_call(
                            self.codegen, enum_type, expr.member, []
                        )

                from sushi_lang.backend.expressions import structs
                return structs.emit_member_access(self.codegen, expr, to_i1)

            case EnumConstructor():
                from sushi_lang.backend.expressions import enums
                return enums.emit_enum_constructor(self.codegen, expr)

            case CastExpr():
                from sushi_lang.backend.expressions import casts
                return casts.emit_cast_expression(self.codegen, expr)

            case Lambda():
                from sushi_lang.backend.runtime import closures
                return closures.emit_lambda(self.codegen, expr, to_i1)

            case _:
                raise NotImplementedError(f"Expression type not supported: {type(expr).__name__}")


__all__ = ['ExpressionEmitter']
