"""
Visitor-based type validation and inference for the Sushi language compiler.

This module implements the Visitor Pattern to replace large match/isinstance
chains in type validation and inference, providing a cleaner, more maintainable
approach to AST type analysis.
"""
from __future__ import annotations
from typing import Optional

from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er
from sushi_lang.semantics.visitors import NodeVisitor, RecursiveVisitor
from sushi_lang.semantics.typesys import Type, BuiltinType, ArrayType, DynamicArrayType, StructType, ForeignPtrType
from sushi_lang.semantics.type_predicates import is_string_convertible
from sushi_lang.semantics.ast import (
    # Statements
    Let, Rebind, ExprStmt, Return, Print, PrintLn, If, While, Foreach, Match, MatchArm, Pattern, Break, Continue,
    # Expressions
    Name, IntLit, FloatLit, BoolLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess,
    UnaryOp, BinaryOp, Call, MethodCall, DotCall, DynamicArrayNew, DynamicArrayFrom, CastExpr, EnumConstructor, TryExpr, RangeExpr, Borrow
)


def function_value_type_of(type_validator, name: str) -> Optional[Type]:
    """Build the FunctionType for a bare reference to a plain top-level function.

    Returns None when `name` is not a referenceable plain function value:
    - not a known top-level function, or
    - a variadic / parameter-pack function (their call ABI differs; deferred).

    The error type defaults to UnknownType("StdError") to mirror fn declarations; it is
    resolved alongside the other members by the normal type-resolution pass.
    """
    from sushi_lang.semantics.typesys import FunctionType, UnknownType
    sig = type_validator.func_table.by_name.get(name)
    if sig is None:
        return None
    for p in sig.params:
        if getattr(p, "is_variadic", False) or getattr(p, "is_pack", False):
            return None
    param_types = tuple(p.ty for p in sig.params)
    if any(pt is None for pt in param_types):
        return None
    ok_type = sig.ret_type if sig.ret_type is not None else BuiltinType.BLANK
    err_type = sig.err_type if sig.err_type is not None else UnknownType("StdError")
    return FunctionType(param_types=param_types, ok_type=ok_type, err_type=err_type)


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

        # Check if the expression evaluates to Result<T, E>
        expr_type = self.type_validator.infer_expression_type(node.expr)
        if expr_type is not None:
            from sushi_lang.semantics.typesys import EnumType, BuiltinType, ResultType

            # Handle ResultType (semantic representation)
            if isinstance(expr_type, ResultType):
                # Skip warning if T is blank type (~)
                # Blank functions have no meaningful return value to handle
                if expr_type.ok_type != BuiltinType.BLANK:
                    er.emit(self.type_validator.reporter, er.ERR.CW2001, node.expr.loc)
            # Handle EnumType (monomorphized representation)
            elif isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
                # Extract T from Result<T, E>
                ok_variant = expr_type.get_variant("Ok")
                if ok_variant and ok_variant.associated_types:
                    t_type = ok_variant.associated_types[0]

                    # Skip warning if T is blank type (~)
                    # Blank functions have no meaningful return value to handle
                    if t_type == BuiltinType.BLANK:
                        return

                # Emit warning for unused Result<T, E> (where T is not blank)
                er.emit(self.type_validator.reporter, er.ERR.CW2001, node.expr.loc)

    def visit_print(self, node: Print) -> None:
        """Validate print statement."""
        self.type_validator.validate_expression(node.value)

        # Check if trying to print Result<T> directly (CE2037)
        expr_type = self.type_validator.infer_expression_type(node.value)
        if expr_type is not None:
            from sushi_lang.semantics.typesys import EnumType
            if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
                er.emit(self.type_validator.reporter, er.ERR.CE2037, node.value.loc)

    def visit_println(self, node: PrintLn) -> None:
        """Validate println statement."""
        self.type_validator.validate_expression(node.value)

        # Check if trying to print Result<T> directly (CE2037)
        expr_type = self.type_validator.infer_expression_type(node.value)
        if expr_type is not None:
            from sushi_lang.semantics.typesys import EnumType
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
        # CE5010: a foreign ptr is an opaque handle - no negation, NOT, or truthiness
        operand_type = self.type_validator.infer_expression_type(node.expr)
        if isinstance(operand_type, ForeignPtrType):
            er.emit(self.type_validator.reporter, er.ERR.CE5010, node.loc, op=node.op)
            return

        # A negated integer literal is range-checked as one signed value, so
        # that i32 min (-2147483648) stays legal while the positive literal
        # 2147483648 alone would not be.
        if node.op == "neg" and isinstance(node.expr, IntLit):
            if not getattr(node.expr, 'in_cast_context', False):
                if -int(node.expr.value) < -(2 ** 31):
                    self._emit_literal_overflow(node.expr)
            node.expr.range_checked = True

        self.type_validator.validate_expression(node.expr)

        # Additional validation for bitwise NOT operator
        if node.op == "~":
            from sushi_lang.semantics.passes.types.expressions import validate_bitwise_unary
            validate_bitwise_unary(self.type_validator, node)

    def visit_binaryop(self, node: BinaryOp) -> None:
        """Validate binary operation."""
        self.type_validator.validate_expression(node.left)
        self.type_validator.validate_expression(node.right)

        left_type = self.type_validator.infer_expression_type(node.left)
        right_type = self.type_validator.infer_expression_type(node.right)

        # CE5010: a foreign ptr is an opaque handle - no comparison, arithmetic,
        # bitwise, or logical operations of any kind.
        if isinstance(left_type, ForeignPtrType) or isinstance(right_type, ForeignPtrType):
            er.emit(self.type_validator.reporter, er.ERR.CE5010, node.loc, op=node.op)
            return

        # Check for string concatenation with + operator (CE2509)
        if node.op == "+":
            # Emit error if either operand is a string
            if left_type == BuiltinType.STRING or right_type == BuiltinType.STRING:
                er.emit_with(self.type_validator.reporter, er.ERR.CE2509, node.loc) \
                    .help("use string interpolation: \"{a}{b}\"").emit()

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
        # FFI: foreign namespace call (e.g., libc.strlen) - NEW FIRST branch.
        # Locals shadow namespaces, so skip if the name is a bound local.
        if self.type_validator._resolve_external_call(node):
            for arg in node.args:
                self.type_validator.validate_expression(arg)
            self.type_validator._validate_external_call_args(node)
            return

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
                from sushi_lang.semantics.ast import EnumConstructor
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
        from sushi_lang.semantics.ast import MethodCall
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

        # CRITICAL: Copy resolved_enum_type back to the DotCall node for codegen
        # This is set by Result<T>/Maybe<T> method validation
        if hasattr(temp_method_call, 'resolved_enum_type') and temp_method_call.resolved_enum_type is not None:
            node.resolved_enum_type = temp_method_call.resolved_enum_type

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
        from sushi_lang.semantics.passes.types.expressions import validate_range_expression
        validate_range_expression(self.type_validator, node)

    # Terminal expressions don't need recursive validation
    def visit_name(self, node: Name) -> None:
        """Name expressions are terminal.

        The one check here is the first-class-function v1 boundary: referencing a
        *generic* function as a value is not supported yet (CE2093). A local of the
        same name shadows the function and is a plain variable read.
        """
        tv = self.type_validator
        if node.id in tv.variable_types or node.id in tv.const_table.by_name:
            return
        if node.id in tv.generic_func_table.by_name:
            er.emit(tv.reporter, er.ERR.CE2093, node.loc,
                    name=node.id, reason="generic function references are deferred (v1)")

    def visit_intlit(self, node: IntLit) -> None:
        """Range-check a bare integer literal (CE2070).

        Literals default to i32, so outside a direct integer `as` cast a
        decimal literal must fit the signed i32 range and a radix literal
        (hex/binary/octal) must fit the 32-bit pattern. Inside a direct cast
        the literal materializes at the target width instead (Rust `as`
        semantics) and is exempt. Negated literals are pre-checked (and
        marked) by visit_unaryop as a single signed value.
        """
        if getattr(node, 'in_cast_context', False) or getattr(node, 'range_checked', False):
            return
        # A context-typed literal was already range-checked against its stamped type
        # at propagation time; skip the default-i32 overflow check.
        if node.resolved_type is not None:
            return
        value = int(node.value)
        if node.radix == 10:
            in_range = 0 <= value <= 2 ** 31 - 1
        else:
            # Bit-pattern semantics: 0xFFFFFFFF is a legal 32-bit pattern
            in_range = 0 <= value <= 2 ** 32 - 1
        if not in_range:
            self._emit_literal_overflow(node)

    def _emit_literal_overflow(self, node: IntLit) -> None:
        """Emit CE2070 for an integer literal that cannot fit its default i32 type."""
        radix_names = {2: "binary", 8: "octal", 10: "decimal", 16: "hexadecimal"}
        er.emit(self.type_validator.reporter, er.ERR.CE2070, node.loc,
                radix=radix_names.get(node.radix, "integer"),
                literal=str(node.value), type="i32")

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

    # === Utility methods ===

    def _resolve_generic_to_semantic_type(self, generic_type: 'Type') -> 'Type':
        """Resolve GenericTypeRef to semantic types (ResultType, etc.) where applicable.

        This centralizes the conversion logic for special generic types that have
        semantic representations beyond simple monomorphization.

        Args:
            generic_type: The type to potentially resolve (may be GenericTypeRef or other)

        Returns:
            Resolved semantic type (ResultType, etc.) or original type if no resolution needed

        Examples:
            GenericTypeRef("Result", [i32, MyError]) → ResultType(i32, MyError)
            GenericTypeRef("Maybe", [i32]) → GenericTypeRef("Maybe", [i32])  # no change
        """
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.typesys import ResultType
        from sushi_lang.semantics.type_resolution import resolve_unknown_type

        # Only process GenericTypeRef types
        if not isinstance(generic_type, GenericTypeRef):
            return generic_type

        # Special handling for Result<T, E> - convert to ResultType
        if generic_type.base_name == "Result" and len(generic_type.type_args) == 2:
            # Recursively resolve type arguments in case they're also generic
            ok_type = resolve_unknown_type(
                generic_type.type_args[0],
                self.type_validator.struct_table.by_name,
                self.type_validator.enum_table.by_name
            )
            err_type = resolve_unknown_type(
                generic_type.type_args[1],
                self.type_validator.struct_table.by_name,
                self.type_validator.enum_table.by_name
            )
            return ResultType(ok_type=ok_type, err_type=err_type)

        # For other generic types (Maybe, Own, etc.), return as-is
        # They will be handled by monomorphization
        return generic_type

    # === Type inference methods ===

    def visit_intlit(self, node: IntLit) -> Optional[Type]:
        """Infer integer literal type (context-typed if stamped, else default i32)."""
        return node.resolved_type or BuiltinType.I32

    def visit_floatlit(self, node: FloatLit) -> Optional[Type]:
        """Infer float literal type (context-typed if stamped, else default f64)."""
        return node.resolved_type or BuiltinType.F64

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
        """Infer member access type (struct field access).

        For fields with generic types like Result<T, E>, this resolves them to their
        semantic type representations (ResultType) for compatibility with pattern matching
        and other type operations.
        """
        # Get the type of the receiver (the struct)
        receiver_type = self.type_validator.infer_expression_type(node.receiver)

        if receiver_type is None:
            return None

        # If it's a struct type, look up the field type
        if isinstance(receiver_type, StructType):
            # Fields are stored as tuples of (field_name, field_type)
            for field_name, field_type in receiver_type.fields:
                if field_name == node.member:
                    # Resolve generic types to semantic types where applicable
                    # E.g., GenericTypeRef("Result", [T, E]) → ResultType(T, E)
                    # This ensures pattern matching and other operations work correctly
                    resolved_type = self._resolve_generic_to_semantic_type(field_type)
                    return resolved_type

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
            from sushi_lang.sushi_stdlib.src import math as math_module
            if math_module.is_builtin_math_constant(node.id):
                return BuiltinType.F64

        # Look up variable type from variable table
        var_type = self.type_validator.variable_types.get(node.id)
        if var_type is not None:
            # Auto-dereference reference types - using a reference variable
            # yields the referenced value, not the reference itself
            from sushi_lang.semantics.typesys import ReferenceType
            if isinstance(var_type, ReferenceType):
                return var_type.referenced_type
            return var_type

        # If not found in variables, check constants
        if node.id in self.type_validator.const_table.by_name:
            const_sig = self.type_validator.const_table.by_name[node.id]
            return const_sig.const_type

        # A bare reference to a plain top-level function is a first-class function value.
        fn_value_type = function_value_type_of(self.type_validator, node.id)
        if fn_value_type is not None:
            return fn_value_type

        return None

    def visit_unaryop(self, node: UnaryOp) -> Optional[Type]:
        """Infer unary operation type."""
        # Logical NOT returns bool
        if node.op == "not":
            return BuiltinType.BOOL
        # Bitwise NOT preserves the integer operand type
        if node.op == "~":
            return self.type_validator.infer_expression_type(node.expr)
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

            # Strict same-type rule: the result is the common operand type.
            # Mixed numeric operands are a CE2510 error (emitted by the
            # ExpressionValidator); return None there to avoid cascading
            # mismatch errors on the enclosing expression.
            numeric = (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                       BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
                       BuiltinType.F32, BuiltinType.F64)
            if left_type == right_type and left_type in numeric:
                return left_type
            # One side unknown (e.g. an unresolved call): trust the known side
            if left_type is None and right_type in numeric:
                return right_type
            if right_type is None and left_type in numeric:
                return left_type
            return None

        # Bitwise operators return the type of the left operand
        if node.op in ["&", "|", "^", "<<", ">>"]:
            return self.type_validator.infer_expression_type(node.left)

        return None

    def visit_call(self, node: Call) -> Optional[Type]:
        """Infer function call type."""
        # Look up function return type
        function_name = node.callee.id

        # Indirect call through a first-class function value: yields Result<ok, err>,
        # exactly like a direct call (so `f(x)??` unwraps to ok_type).
        from sushi_lang.semantics.typesys import FunctionType, ResultType
        callee_var_ty = self.type_validator.variable_types.get(function_name)
        if isinstance(callee_var_ty, FunctionType):
            return ResultType(ok_type=callee_var_ty.ok_type, err_type=callee_var_ty.err_type)

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

        # Check for io/files module functions
        if function_name in {'exists', 'is_file', 'is_dir'}:
            # These functions return bool (i8)
            return BuiltinType.BOOL
        if function_name == 'file_size':
            # file_size() returns Result<i64, FileError>
            from sushi_lang.semantics.typesys import ResultType, UnknownType
            from sushi_lang.semantics.type_resolution import resolve_unknown_type

            # Get FileError enum type
            file_error = self.type_validator.enum_table.by_name.get("FileError")
            if file_error is None:
                # Fallback to UnknownType if FileError not registered yet
                file_error = UnknownType("FileError")

            # Return ResultType - it will be resolved to EnumType when needed
            return ResultType(ok_type=BuiltinType.I64, err_type=file_error)
        if function_name in {'remove', 'rename', 'copy', 'mkdir', 'rmdir'}:
            # These functions return Result<i32, FileError>
            from sushi_lang.semantics.typesys import ResultType, UnknownType
            from sushi_lang.semantics.type_resolution import resolve_unknown_type

            # Get FileError enum type
            file_error = self.type_validator.enum_table.by_name.get("FileError")
            if file_error is None:
                # Fallback to UnknownType if FileError not registered yet
                file_error = UnknownType("FileError")

            # Return ResultType - it will be resolved to EnumType when needed
            return ResultType(ok_type=BuiltinType.I32, err_type=file_error)

        # Check for math module functions
        if function_name in {'abs', 'min', 'max', 'sqrt', 'pow', 'floor', 'ceil', 'round', 'trunc'}:
            from sushi_lang.sushi_stdlib.src import math as math_module
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
            # Functions can declare explicit Result<T, E> or just T (implicit Result<T, StdError>)
            if func_sig.ret_type is not None:
                from sushi_lang.semantics.generics.types import GenericTypeRef
                from sushi_lang.semantics.typesys import ResultType
                from sushi_lang.semantics.type_resolution import resolve_unknown_type

                # If function already returns ResultType, return it as-is
                if isinstance(func_sig.ret_type, ResultType):
                    return func_sig.ret_type

                # If function declares Result<T, E>, resolve and return it
                if isinstance(func_sig.ret_type, GenericTypeRef) and func_sig.ret_type.base_name == "Result":
                    # Resolve GenericTypeRef("Result") to ResultType
                    return resolve_unknown_type(
                        func_sig.ret_type,
                        self.type_validator.struct_table.by_name,
                        self.type_validator.enum_table.by_name
                    )
                elif func_sig.err_type is not None:
                    # Implicit Result syntax: fn foo() i32 | MyError
                    # Construct ResultType(ok_type=i32, err_type=MyError)
                    err_type = resolve_unknown_type(
                        func_sig.err_type,
                        self.type_validator.struct_table.by_name,
                        self.type_validator.enum_table.by_name
                    )
                    return ResultType(ok_type=func_sig.ret_type, err_type=err_type)
                else:
                    # Default implicit Result syntax: fn foo() i32 (defaults to StdError)
                    # Construct ResultType(ok_type=i32, err_type=StdError)
                    err_type = self.type_validator.enum_table.by_name.get("StdError")
                    if err_type is not None:
                        return ResultType(ok_type=func_sig.ret_type, err_type=err_type)
                    # Fallback to old behavior if StdError not found
                    result_enum_name = f"Result<{func_sig.ret_type}>"
                    if result_enum_name in self.type_validator.enum_table.by_name:
                        return self.type_validator.enum_table.by_name[result_enum_name]
                # Fallback to declared return type
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
        from sushi_lang.semantics.typesys import StructType, EnumType, ReferenceType


        # Unwrap ReferenceType to get the underlying type
        # Methods on &T are the same as methods on T
        actual_type = receiver_type
        if isinstance(receiver_type, ReferenceType):
            actual_type = receiver_type.referenced_type

        # Handle GenericTypeRef by resolving to actual StructType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(actual_type, GenericTypeRef):
            type_args_str = ", ".join(str(arg) for arg in actual_type.type_args)
            type_name = f"{actual_type.base_name}<{type_args_str}>"
            if type_name in self.type_validator.struct_table.by_name:
                actual_type = self.type_validator.struct_table.by_name[type_name]
            elif type_name in self.type_validator.enum_table.by_name:
                actual_type = self.type_validator.enum_table.by_name[type_name]

        if actual_type is not None and isinstance(actual_type, (BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType)):
            # Try the method type registry first (handles all built-in types)
            from sushi_lang.semantics.method_type_registry import METHOD_TYPE_REGISTRY
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
        # FFI: foreign namespace call - the raw C return type stands verbatim.
        sig = self.type_validator._resolve_external_call(node)
        if sig is not None:
            node.inferred_return_type = sig.ret_type
            return sig.ret_type

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
        from sushi_lang.semantics.ast import MethodCall
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
        from sushi_lang.semantics.typesys import EnumType
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
        from sushi_lang.semantics.passes.types.inference import infer_range_expression_type
        return infer_range_expression_type(self.type_validator, node)

    def visit_borrow(self, node: Borrow) -> Optional[Type]:
        """Infer type of borrow expression (&peek expr or &poke expr).

        Returns ReferenceType with the correct mutability based on the
        borrow mode (peek or poke) specified in the node.
        """
        from sushi_lang.semantics.typesys import ReferenceType, BorrowMode

        # Get the type of the borrowed expression
        inner_type = self.type_validator.infer_expression_type(node.expr)
        if inner_type is None:
            return None

        # Create ReferenceType with the correct mutability
        mutability = BorrowMode.PEEK if node.mutability == "peek" else BorrowMode.POKE
        return ReferenceType(referenced_type=inner_type, mutability=mutability)

    def generic_visit(self, node) -> Optional[Type]:
        """Default behavior for unknown nodes."""
        return None
