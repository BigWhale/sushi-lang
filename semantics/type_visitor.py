"""
Visitor-based type validation and inference for the Sushi language compiler.

This module implements the Visitor Pattern to replace large match/isinstance
chains in type validation and inference, providing a cleaner, more maintainable
approach to AST type analysis.
"""
from __future__ import annotations
from typing import Optional

from internals.report import Reporter
from internals import errors as er
from semantics.visitors import NodeVisitor, RecursiveVisitor
from semantics.typesys import Type, BuiltinType, ArrayType, DynamicArrayType, StructType
from semantics.type_predicates import is_string_convertible
from semantics.ast import (
    # Statements
    Let, Rebind, ExprStmt, Return, Print, PrintLn, If, While, Foreach, Match, MatchArm, Pattern, Break, Continue,
    # Expressions
    Name, IntLit, FloatLit, BoolLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess,
    UnaryOp, BinaryOp, Call, MethodCall, DotCall, DynamicArrayNew, DynamicArrayFrom, CastExpr, EnumConstructor, TryExpr, RangeExpr
)


class StatementValidator(RecursiveVisitor):
    """
    Visitor for validating statements using the Visitor Pattern.

    Replaces the large match statement in TypeValidator._validate_statement()
    with clean, focused methods for each statement type.
    """

    def __init__(self, type_validator: 'TypeValidator'):
        """Initialize with reference to the main type validator."""
        self.type_validator = type_validator

    # === Statement validation methods ===

    def visit_if(self, node: If) -> None:
        """Validate if statement conditions and branches."""
        # Validate all condition-block arms
        for cond, block in node.arms:
            # Validate condition is boolean (CE2005)
            self.type_validator._validate_boolean_condition(cond, "if")
            # Validate block
            self.type_validator._validate_block(block)

        # Validate else branch if present
        if node.else_block:
            self.type_validator._validate_block(node.else_block)

    def visit_while(self, node: While) -> None:
        """Validate while statement condition and body."""
        # Validate condition is boolean (CE2005)
        self.type_validator._validate_boolean_condition(node.cond, "while")
        # Validate body
        self.type_validator._validate_block(node.body)

    def visit_foreach(self, node: Foreach) -> None:
        """Validate foreach statement iterator type and body."""
        self.type_validator._validate_foreach_statement(node)

    def visit_match(self, node: Match) -> None:
        """Validate match statement with exhaustiveness checking."""
        self.type_validator._validate_match_statement(node)

    def visit_let(self, node: Let) -> None:
        """Validate let statement."""
        self.type_validator._validate_let_statement(node)

    def visit_return(self, node: Return) -> None:
        """Validate return statement."""
        self.type_validator._validate_return_statement(node)

    def visit_exprstmt(self, node: ExprStmt) -> None:
        """Validate expression statement and warn if Result<T> is unused."""
        # First validate the expression
        self.type_validator.validate_expression(node.expr)

        # Check if the expression evaluates to Result<T>
        expr_type = self.type_validator.infer_expression_type(node.expr)
        if expr_type is not None:
            from semantics.typesys import EnumType, BuiltinType
            if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
                # Extract T from Result<T>
                ok_variant = expr_type.get_variant("Ok")
                if ok_variant and ok_variant.associated_types:
                    t_type = ok_variant.associated_types[0]

                    # Skip warning if T is blank type (~)
                    # Blank functions have no meaningful return value to handle
                    if t_type == BuiltinType.BLANK:
                        return

                # Emit warning for unused Result<T> (where T is not blank)
                er.emit(self.type_validator.reporter, er.ERR.CW2001, node.expr.loc)

    def visit_print(self, node: Print) -> None:
        """Validate print statement."""
        self.type_validator.validate_expression(node.value)

        # Check if trying to print Result<T> directly (CE2037)
        expr_type = self.type_validator.infer_expression_type(node.value)
        if expr_type is not None:
            from semantics.typesys import EnumType
            if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
                er.emit(self.type_validator.reporter, er.ERR.CE2037, node.value.loc)

    def visit_println(self, node: PrintLn) -> None:
        """Validate println statement."""
        self.type_validator.validate_expression(node.value)

        # Check if trying to print Result<T> directly (CE2037)
        expr_type = self.type_validator.infer_expression_type(node.value)
        if expr_type is not None:
            from semantics.typesys import EnumType
            if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
                er.emit(self.type_validator.reporter, er.ERR.CE2037, node.value.loc)

    def visit_rebind(self, node: Rebind) -> None:
        """Validate rebind statement."""
        self.type_validator._validate_rebind_statement(node)

    def visit_break(self, node: Break) -> None:
        """Break statements don't need type validation."""
        pass

    def visit_continue(self, node: Continue) -> None:
        """Continue statements don't need type validation."""
        pass


class ExpressionValidator(RecursiveVisitor):
    """
    Visitor for validating expressions using the Visitor Pattern.

    Replaces the large match statement in TypeValidator._validate_expression()
    with clean, focused methods for each expression type.
    """

    def __init__(self, type_validator: 'TypeValidator'):
        """Initialize with reference to the main type validator."""
        self.type_validator = type_validator

    # === Expression validation methods ===

    def visit_unaryop(self, node: UnaryOp) -> None:
        """Validate unary operation."""
        self.type_validator.validate_expression(node.expr)

        # Additional validation for bitwise NOT operator
        if node.op == "~":
            from semantics.passes.types.expressions import validate_bitwise_unary
            validate_bitwise_unary(self.type_validator, node)

    def visit_binaryop(self, node: BinaryOp) -> None:
        """Validate binary operation."""
        self.type_validator.validate_expression(node.left)
        self.type_validator.validate_expression(node.right)

        left_type = self.type_validator.infer_expression_type(node.left)
        right_type = self.type_validator.infer_expression_type(node.right)

        # Check for string concatenation with + operator (CE2509)
        if node.op == "+":
            # Emit error if either operand is a string
            if left_type == BuiltinType.STRING or right_type == BuiltinType.STRING:
                er.emit(self.type_validator.reporter, er.ERR.CE2509, node.loc)

        # Check for mixed numeric types in comparison and arithmetic operations
        if node.op in ["==", "!=", "<", "<=", ">", ">=", "+", "-", "*", "/", "%"]:
            if left_type is not None and right_type is not None:
                # Check if both are numeric types but different
                left_is_numeric = isinstance(left_type, BuiltinType) and left_type in [
                    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
                    BuiltinType.F32, BuiltinType.F64
                ]
                right_is_numeric = isinstance(right_type, BuiltinType) and right_type in [
                    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
                    BuiltinType.F32, BuiltinType.F64
                ]

                if left_is_numeric and right_is_numeric and left_type != right_type:
                    # Mixed numeric types - require explicit cast
                    er.emit(self.type_validator.reporter, er.ERR.CE2510, node.loc,
                           left_type=str(left_type), right_type=str(right_type))

        # Additional validation for bitwise operators
        if node.op in ["&", "|", "^", "<<", ">>"]:
            self.type_validator._validate_bitwise_operation(node)

    def visit_call(self, node: Call) -> None:
        """Validate function call."""
        self.type_validator._validate_function_call(node)

    def visit_methodcall(self, node: MethodCall) -> None:
        """Validate method call."""
        self.type_validator._validate_method_call(node)

    def visit_dotcall(self, node: DotCall) -> None:
        """Validate dot-call expression - resolve to enum constructor or method call."""
        # Validate receiver first
        self.type_validator.validate_expression(node.receiver)

        # Check if receiver is an enum type name
        if isinstance(node.receiver, Name):
            receiver_name = node.receiver.id
            # Check if it's an enum type (concrete or generic)
            if (receiver_name in self.type_validator.enum_table.by_name or
                receiver_name in self.type_validator.generic_enum_table.by_name):
                # This is an enum constructor - validate as such
                # Convert to EnumConstructor for validation
                from semantics.ast import EnumConstructor
                temp_constructor = EnumConstructor(
                    enum_name=receiver_name,
                    variant_name=node.method,
                    args=node.args,
                    enum_name_span=node.receiver.loc,
                    loc=node.loc
                )

                # CRITICAL: Copy resolved_enum_type FROM the DotCall TO the temp BEFORE validation
                # This is set by _validate_return_statement or _validate_let_statement
                if hasattr(node, 'resolved_enum_type') and node.resolved_enum_type is not None:
                    temp_constructor.resolved_enum_type = node.resolved_enum_type

                self.type_validator._validate_enum_constructor(temp_constructor)

                # CRITICAL: Copy resolved_enum_type back to the DotCall node for codegen
                # (in case validation set or updated it)
                if hasattr(temp_constructor, 'resolved_enum_type'):
                    node.resolved_enum_type = temp_constructor.resolved_enum_type
                return

        # Otherwise, it's a method call - validate as such
        # Convert to MethodCall for validation
        from semantics.ast import MethodCall
        temp_method_call = MethodCall(
            receiver=node.receiver,
            method=node.method,
            args=node.args,
            loc=node.loc
        )
        self.type_validator._validate_method_call(temp_method_call)

        # CRITICAL: Copy inferred_return_type back to the DotCall node for codegen
        # This is set by perk/extension method validation
        if hasattr(temp_method_call, 'inferred_return_type') and temp_method_call.inferred_return_type is not None:
            node.inferred_return_type = temp_method_call.inferred_return_type

    def visit_arrayliteral(self, node: ArrayLiteral) -> None:
        """Validate array literal."""
        self.type_validator._validate_array_literal(node)

    def visit_indexaccess(self, node: IndexAccess) -> None:
        """Validate index access."""
        self.type_validator._validate_index_access(node)

    def visit_dynamicarraynew(self, node: DynamicArrayNew) -> None:
        """new() constructor - no subexpressions to validate."""
        pass

    def visit_dynamicarrayfrom(self, node: DynamicArrayFrom) -> None:
        """from(array_literal) - validate the array literal."""
        self.type_validator.validate_expression(node.elements)

    def visit_castexpr(self, node: CastExpr) -> None:
        """Cast expression - validate the source expression and check cast validity."""
        self.type_validator._validate_cast_expression(node)

    def visit_enumconstructor(self, node: EnumConstructor) -> None:
        """Validate enum constructor call (including Result.Ok() and Result.Err())."""
        self.type_validator._validate_enum_constructor(node)

    def visit_tryexpr(self, node: TryExpr) -> None:
        """Validate try expression (?? operator)."""
        self.type_validator._validate_try_expression(node)

    def visit_rangeexpr(self, node: RangeExpr) -> None:
        """Validate range expression."""
        from semantics.passes.types.expressions import validate_range_expression
        validate_range_expression(self.type_validator, node)

    # Terminal expressions don't need recursive validation
    def visit_name(self, node: Name) -> None:
        """Name expressions are terminal."""
        pass

    def visit_intlit(self, node: IntLit) -> None:
        """Integer literals are terminal."""
        pass

    def visit_floatlit(self, node: FloatLit) -> None:
        """Float literals are terminal."""
        pass

    def visit_boollit(self, node: BoolLit) -> None:
        """Boolean literals are terminal."""
        pass

    def visit_stringlit(self, node: StringLit) -> None:
        """String literals are terminal."""
        pass

    def visit_interpolatedstring(self, node: InterpolatedString) -> None:
        """Visit expressions in interpolated string."""
        for part in node.parts:
            if not isinstance(part, str):  # part is an Expr
                self.visit(part)


class TypeInferenceVisitor(NodeVisitor[Optional[Type]]):
    """
    Visitor for type inference using the Visitor Pattern.

    Replaces the large match statement in TypeValidator._infer_expression_type()
    with clean, focused methods for each expression type.
    """

    def __init__(self, type_validator: 'TypeValidator'):
        """Initialize with reference to the main type validator."""
        self.type_validator = type_validator

    # === Type inference methods ===

    def visit_intlit(self, node: IntLit) -> Optional[Type]:
        """Infer integer literal type."""
        return BuiltinType.I32

    def visit_floatlit(self, node: FloatLit) -> Optional[Type]:
        """Infer float literal type."""
        return BuiltinType.F64  # Default floating literals to f64

    def visit_boollit(self, node: BoolLit) -> Optional[Type]:
        """Infer boolean literal type."""
        return BuiltinType.BOOL

    def visit_blanklit(self, node: 'BlankLit') -> Optional[Type]:
        """Infer blank literal type."""
        return BuiltinType.BLANK

    def visit_stringlit(self, node: StringLit) -> Optional[Type]:
        """Infer string literal type."""
        return BuiltinType.STRING

    def visit_interpolatedstring(self, node: InterpolatedString) -> Optional[Type]:
        """Infer interpolated string type and validate expression types."""
        # Validate that all expression parts can be converted to strings
        for part in node.parts:
            if not isinstance(part, str):
                # This is an expression - validate it can be converted to string
                expr_type = self.type_validator.infer_expression_type(part)
                if expr_type and not is_string_convertible(expr_type):
                    # Emit error for unsupported type in interpolation
                    er.emit(
                        self.type_validator.reporter,
                        er.ERR.CE2035,
                        part.loc,
                        type=str(expr_type)
                    )
        return BuiltinType.STRING

    def visit_arrayliteral(self, node: ArrayLiteral) -> Optional[Type]:
        """Infer array literal type."""
        return self.type_validator._infer_array_literal_type(node)

    def visit_indexaccess(self, node: IndexAccess) -> Optional[Type]:
        """Infer index access type."""
        return self.type_validator._infer_index_access_type(node)

    def visit_memberaccess(self, node: MemberAccess) -> Optional[Type]:
        """Infer member access type (struct field access)."""
        # Get the type of the receiver (the struct)
        receiver_type = self.type_validator.infer_expression_type(node.receiver)

        if receiver_type is None:
            return None

        # If it's a struct type, look up the field type
        if isinstance(receiver_type, StructType):
            # Fields are stored as tuples of (field_name, field_type)
            for field_name, field_type in receiver_type.fields:
                if field_name == node.member:
                    return field_type

        return None

    def visit_name(self, node: Name) -> Optional[Type]:
        """Infer name expression type."""
        # Check for special built-in identifiers first (stdin, stdout, stderr)
        if node.id == "stdin":
            return BuiltinType.STDIN
        elif node.id == "stdout":
            return BuiltinType.STDOUT
        elif node.id == "stderr":
            return BuiltinType.STDERR

        # Check for math module constants (PI, E, TAU)
        if node.id in {'PI', 'E', 'TAU'}:
            from stdlib.src import math as math_module
            if math_module.is_builtin_math_constant(node.id):
                return BuiltinType.F64

        # Look up variable type from variable table
        var_type = self.type_validator.variable_types.get(node.id)
        if var_type is not None:
            return var_type

        # If not found in variables, check constants
        if node.id in self.type_validator.const_table.by_name:
            const_sig = self.type_validator.const_table.by_name[node.id]
            return const_sig.const_type

        return None

    def visit_unaryop(self, node: UnaryOp) -> Optional[Type]:
        """Infer unary operation type."""
        # Logical NOT returns bool
        if node.op == "not":
            return BuiltinType.BOOL
        # Bitwise NOT returns i32
        if node.op == "~":
            return BuiltinType.I32
        # Negation preserves numeric type
        if node.op == "neg":
            return self.type_validator.infer_expression_type(node.expr)
        # Default: preserve type
        return self.type_validator.infer_expression_type(node.expr)

    def visit_binaryop(self, node: BinaryOp) -> Optional[Type]:
        """Infer binary operation type directly (no delegation)."""
        # Comparison operators return bool
        if node.op in ["==", "!=", "<", "<=", ">", ">="]:
            return BuiltinType.BOOL

        # Logical operators return bool
        if node.op in ["and", "or", "xor"]:
            return BuiltinType.BOOL

        # Arithmetic operators - return type depends on operands
        if node.op in ["+", "-", "*", "/", "%"]:
            left_type = self.type_validator.infer_expression_type(node.left)
            right_type = self.type_validator.infer_expression_type(node.right)

            # If either operand is a string, this is an error (handled by validation)
            # Return None to avoid cascading type mismatch errors
            if left_type == BuiltinType.STRING or right_type == BuiltinType.STRING:
                return None

            # If either operand is floating-point, return f64
            if (left_type in [BuiltinType.F32, BuiltinType.F64] or
                right_type in [BuiltinType.F32, BuiltinType.F64]):
                return BuiltinType.F64
            # If both are integers, return i32
            return BuiltinType.I32

        # Bitwise operators return the type of the left operand
        if node.op in ["&", "|", "^", "<<", ">>"]:
            return self.type_validator.infer_expression_type(node.left)

        return None

    def visit_call(self, node: Call) -> Optional[Type]:
        """Infer function call type."""
        # Look up function return type
        function_name = node.callee.id

        # Check if this is a struct constructor
        if function_name in self.type_validator.struct_table.by_name:
            return self.type_validator.struct_table.by_name[function_name]

        # Check for built-in global functions
        if function_name == "open":
            # open() returns FileResult enum
            return self.type_validator.enum_table.by_name.get("FileResult")

        # Check for time module functions
        if function_name in {'sleep', 'msleep', 'usleep', 'nanosleep'}:
            # All time functions return Result<i32>
            result_i32_name = "Result<i32>"
            if result_i32_name in self.type_validator.enum_table.by_name:
                return self.type_validator.enum_table.by_name[result_i32_name]
            return None

        # Check for sys/env module functions
        if function_name == 'getenv':
            # getenv() returns Maybe<string>
            maybe_string_name = "Maybe<string>"
            if maybe_string_name in self.type_validator.enum_table.by_name:
                return self.type_validator.enum_table.by_name[maybe_string_name]
            return None
        if function_name == 'setenv':
            # setenv() returns Result<i32>
            result_i32_name = "Result<i32>"
            if result_i32_name in self.type_validator.enum_table.by_name:
                return self.type_validator.enum_table.by_name[result_i32_name]
            return None

        # Check for math module functions
        if function_name in {'abs', 'min', 'max', 'sqrt', 'pow', 'floor', 'ceil', 'round', 'trunc'}:
            from stdlib.src import math as math_module
            if math_module.is_builtin_math_function(function_name):
                # Get the parameter types to determine return type
                param_types = []
                for arg in node.args:
                    arg_type = self.type_validator.infer_expression_type(arg)
                    if arg_type is not None:
                        param_types.append(arg_type)

                # abs, min, max return the same type as their input(s)
                if function_name in {'abs', 'min', 'max'} and param_types:
                    return param_types[0]

                # sqrt, pow, floor, ceil, round, trunc always return f64
                if function_name in {'sqrt', 'pow', 'floor', 'ceil', 'round', 'trunc'}:
                    return BuiltinType.F64

            return None

        # Otherwise, check if it's a function call
        if function_name in self.type_validator.func_table.by_name:
            func_sig = self.type_validator.func_table.by_name[function_name]
            # All functions implicitly return Result<T> where T is the declared return type
            # Look up the monomorphized Result<T> in the enum table
            if func_sig.ret_type is not None:
                result_enum_name = f"Result<{func_sig.ret_type}>"
                if result_enum_name in self.type_validator.enum_table.by_name:
                    return self.type_validator.enum_table.by_name[result_enum_name]
            # Fallback to declared return type (shouldn't happen after monomorphization)
            return func_sig.ret_type
        return None

    def visit_methodcall(self, node: MethodCall) -> Optional[Type]:
        """Infer method call type and annotate node with inferred return type."""
        # Check if this is actually an enum constructor (like Result.Ok())
        # In the new parsing, these are MethodCall nodes, not EnumConstructor nodes
        if isinstance(node.receiver, Name):
            enum_name = node.receiver.id
            # Check if the receiver is an enum type
            if enum_name in self.type_validator.enum_table.by_name:
                # This is an enum constructor, return the enum type
                inferred_type = self.type_validator.enum_table.by_name[enum_name]
                node.inferred_return_type = inferred_type
                return inferred_type
            elif enum_name in self.type_validator.generic_enum_table.by_name:
                # This is a generic enum constructor (like Result.Ok())
                # We can't infer the complete type without more context
                # For now, return None and let the type be inferred from context
                return None

        # Look up method return type using the registry pattern
        receiver_type = self.type_validator.infer_expression_type(node.receiver)
        from semantics.typesys import StructType, EnumType, ReferenceType

        # Unwrap ReferenceType to get the underlying type
        # Methods on &T are the same as methods on T
        actual_type = receiver_type
        if isinstance(receiver_type, ReferenceType):
            actual_type = receiver_type.referenced_type

        if actual_type is not None and isinstance(actual_type, (BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType)):
            # Try the method type registry first (handles all built-in types)
            from semantics.method_type_registry import METHOD_TYPE_REGISTRY
            inferred_type = METHOD_TYPE_REGISTRY.infer_method_type(
                actual_type, node.method, self.type_validator
            )

            # Fall back to perk methods first (higher priority than extensions)
            if inferred_type is None:
                perk_method = self.type_validator.perk_impl_table.get_method(actual_type, node.method)
                if perk_method is not None and perk_method.ret is not None:
                    # Perk methods return bare types (like extension methods)
                    inferred_type = perk_method.ret

            # Fall back to extension table if registry didn't find it
            if inferred_type is None:
                method = self.type_validator.extension_table.get_method(actual_type, node.method)
                if method is not None:
                    inferred_type = method.ret_type

            # Annotate the node with the inferred type
            if inferred_type is not None:
                node.inferred_return_type = inferred_type
                return inferred_type

        return None

    def visit_dotcall(self, node: DotCall) -> Optional[Type]:
        """Infer dot-call type and annotate node with inferred return type."""
        # Check if receiver is an enum type name
        if isinstance(node.receiver, Name):
            receiver_name = node.receiver.id
            # Check if it's an enum type (concrete or generic)
            if receiver_name in self.type_validator.enum_table.by_name:
                # This is an enum constructor - return the enum type
                inferred_type = self.type_validator.enum_table.by_name[receiver_name]
                node.inferred_return_type = inferred_type
                return inferred_type
            elif receiver_name in self.type_validator.generic_enum_table.by_name:
                # This is a generic enum constructor (like Result.Ok())
                # We can't infer the complete type without more context
                # For now, return None and let the type be inferred from context
                return None

        # Otherwise, it's a method call - infer return type from method
        # Convert to MethodCall temporarily for type inference
        from semantics.ast import MethodCall
        temp_method_call = MethodCall(
            receiver=node.receiver,
            method=node.method,
            args=node.args,
            loc=node.loc
        )
        inferred_type = self.visit_methodcall(temp_method_call)
        # Copy the inferred type to the DotCall node
        if inferred_type is not None:
            node.inferred_return_type = inferred_type
        return inferred_type

    def visit_dynamicarraynew(self, node: DynamicArrayNew) -> Optional[Type]:
        """new() constructor requires context for type inference."""
        # This should be handled by the caller that has access to LHS type
        return None

    def visit_dynamicarrayfrom(self, node: DynamicArrayFrom) -> Optional[Type]:
        """from(array_literal) can infer type from array literal elements."""
        return self.type_validator._infer_dynamic_array_from_type(node)

    def visit_castexpr(self, node: CastExpr) -> Optional[Type]:
        """Cast expression - return the target type."""
        return node.target_type

    def visit_enumconstructor(self, node: EnumConstructor) -> Optional[Type]:
        """EnumConstructor - return the enum type (including Result.Ok/Result.Err)."""
        # Check if the node has a resolved enum type (for generic enums like Result<T>)
        # This is set by the type checker during validation
        if hasattr(node, 'resolved_enum_type') and node.resolved_enum_type is not None:
            return node.resolved_enum_type

        # Otherwise, look up the concrete enum type
        if node.enum_name in self.type_validator.enum_table.by_name:
            return self.type_validator.enum_table.by_name[node.enum_name]

        return None

    def visit_tryexpr(self, node: TryExpr) -> Optional[Type]:
        """Try expression (?? operator) - unwrap result-like enum to Ok type.

        Supports any enum with Ok(T) and Err(...) variants, including:
        - Result<T> (generic)
        - FileResult (concrete enum with Ok(file) variant)
        """
        # Infer the type of the inner expression
        inner_type = self.type_validator.infer_expression_type(node.expr)

        if inner_type is None:
            return None

        # Check if it's a result-like enum (has Ok variant with associated type)
        from semantics.typesys import EnumType
        if isinstance(inner_type, EnumType):
            # Extract T from Ok(T) variant
            ok_variant = inner_type.get_variant("Ok")
            if ok_variant and ok_variant.associated_types:
                # Return the unwrapped type T
                return ok_variant.associated_types[0]

        # Not a result-like enum - will be caught by validation
        return None

    def visit_rangeexpr(self, node: RangeExpr) -> Optional[Type]:
        """Infer type of range expression - always Iterator<i32>."""
        from semantics.passes.types.inference import infer_range_expression_type
        return infer_range_expression_type(self.type_validator, node)

    def generic_visit(self, node) -> Optional[Type]:
        """Default behavior for unknown nodes."""
        return None
