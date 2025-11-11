# semantics/generics/instantiate.py
"""
Pass 1.5: Generic Type Instantiation Collector

This pass scans the entire AST and collects all generic type instantiations
(e.g., Result<i32>, Result<MyStruct>) that are used in the program.

This enables monomorphization - generating concrete types only for
instantiations that are actually used, avoiding unnecessary code bloat.
"""
from __future__ import annotations
from typing import Set, Tuple
from dataclasses import dataclass, field

from semantics.ast import Program, FuncDef, ExtendDef, ExtendWithDef, ConstDef, StructDef, Let, Foreach, Param
from semantics.typesys import Type, GenericTypeRef
from semantics.type_resolution import resolve_unknown_type, contains_unresolvable_unknown_type


@dataclass
class InstantiationCollector:
    """Collects all generic type instantiations used in a program.

    Scans function signatures, variable declarations, constants, and all type
    annotations to find generic type references like Result<i32> and Pair<i32, string>.

    The collected instantiations are used by the Monomorphizer to generate
    concrete EnumType and StructType instances for each unique instantiation.
    """

    # Set of (base_name, type_args) tuples representing unique instantiations
    # Examples:
    #   - ("Result", (BuiltinType.I32,)) for Result<i32>
    #   - ("Pair", (BuiltinType.I32, BuiltinType.STRING)) for Pair<i32, string>
    # The base_name distinguishes between generic enums and generic structs.
    instantiations: Set[Tuple[str, Tuple[Type, ...]]] = field(default_factory=set)

    # NEW: Set of (function_name, type_args) tuples for generic function instantiations
    # Examples:
    #   - ("identity", (BuiltinType.I32,)) for identity<i32>
    #   - ("swap", (BuiltinType.I32, BuiltinType.STRING)) for swap<i32, string>
    function_instantiations: Set[Tuple[str, Tuple[Type, ...]]] = field(default_factory=set)

    # Struct table for resolving UnknownType to StructType
    struct_table: Optional[Dict[str, "StructType"]] = field(default=None)

    # Enum table for resolving UnknownType to EnumType
    enum_table: Optional[Dict[str, "EnumType"]] = field(default=None)

    # Generic struct table for checking if a base_name refers to a generic struct
    # This is used to distinguish generic struct instantiations from generic enum instantiations
    generic_structs: Optional[Dict[str, "GenericStructType"]] = field(default=None)

    # NEW: Generic function table for checking if a function name refers to a generic function
    generic_funcs: Optional[Dict[str, "GenericFuncDef"]] = field(default=None)

    # Simple variable type table for tracking explicitly typed variables in current scope
    # Maps variable name -> type for variables with explicit type annotations
    variable_types: Dict[str, Type] = field(default_factory=dict)

    # Track visited types to prevent infinite recursion on recursive types (e.g., Own<Expr> in Expr)
    visited_types: Set[str] = field(default_factory=set)

    def run(self, program: Program) -> Tuple[Set[Tuple[str, Tuple[Type, ...]]], Set[Tuple[str, Tuple[Type, ...]]]]:
        """Entry point for instantiation collection.

        Args:
            program: The program AST to scan

        Returns:
            Tuple of (type instantiations, function instantiations)
            - type instantiations: Set of (base_name, type_args) for generic types
            - function instantiations: Set of (function_name, type_args) for generic functions
        """
        # Collect from constants
        for const in program.constants:
            self._collect_from_const(const)

        # Collect from struct definitions
        # This ensures that generic types used as struct fields (e.g., Maybe<i32>)
        # are properly monomorphized before codegen
        for struct in program.structs:
            self._collect_from_struct(struct)

        # Collect from enum definitions
        # This ensures that generic types used in enum variants (e.g., Own<Expr>)
        # are properly monomorphized before codegen
        for enum in program.enums:
            self._collect_from_enum(enum)

        # Collect from function signatures
        for func in program.functions:
            self._collect_from_function(func)

        # Collect from extension method signatures
        for ext in program.extensions:
            self._collect_from_extension(ext)

        # Collect from perk implementation methods
        # Perk methods return bare types (like extensions), but we still need to
        # collect generic instantiations from their parameters and bodies
        for perk_impl in program.perk_impls:
            self._collect_from_perk_impl(perk_impl)

        return self.instantiations, self.function_instantiations

    def _collect_from_const(self, const: ConstDef) -> None:
        """Collect generic instantiations from constant definition."""
        if const.ty is not None:
            self._collect_from_type(const.ty)

    def _collect_from_struct(self, struct: StructDef) -> None:
        """Collect generic instantiations from struct field types.

        This ensures that generic types used as struct fields (e.g., Maybe<i32>)
        are properly monomorphized before codegen.

        Example:
            struct Point:
                Maybe<i32> x  # Collects Maybe<i32> instantiation
                Maybe<i32> y
        """
        for field in struct.fields:
            if field.ty is not None:
                self._collect_from_type(field.ty)

    def _collect_from_enum(self, enum: EnumDef) -> None:
        """Collect generic instantiations from enum variant associated types.

        This ensures that generic types used in enum variants (e.g., Own<Expr>)
        are properly monomorphized before codegen.

        Example:
            enum Expr:
                IntLit(i32)
                BinOp(Own<Expr>, Own<Expr>, string)  # Collects Own<Expr> instantiation
        """
        for variant in enum.variants:
            for assoc_type in variant.associated_types:
                self._collect_from_type(assoc_type)

    def _collect_from_function(self, func: FuncDef) -> None:
        """Collect generic instantiations from function signature and body."""
        # Skip generic functions entirely - they will be scanned during monomorphization
        if hasattr(func, 'type_params') and func.type_params:
            return

        # Collect from return type
        # IMPORTANT: All functions implicitly return Result<T>, so we need to
        # record Result<T> instantiation for the function's return type
        if func.ret is not None:
            self._collect_from_type(func.ret)
            # Add implicit Result<T> instantiation
            from semantics.generics.types import GenericTypeRef
            result_instantiation = GenericTypeRef(base_name="Result", type_args=(func.ret,))
            self._collect_from_type(result_instantiation)

        # Collect from parameters
        for param in func.params:
            self._collect_from_param(param)

        # Collect from function body (variable declarations)
        self._collect_from_block(func.body)

    def _collect_from_extension(self, ext: ExtendDef) -> None:
        """Collect generic instantiations from extension method signature and body."""
        # Collect from target type
        if ext.target_type is not None:
            self._collect_from_type(ext.target_type)

        # Collect from return type
        # Extension methods return bare types (not Result<T>)
        if ext.ret is not None:
            self._collect_from_type(ext.ret)

        # Collect from parameters
        for param in ext.params:
            self._collect_from_param(param)

        # Collect from method body (variable declarations)
        self._collect_from_block(ext.body)

    def _collect_from_perk_impl(self, perk_impl: "ExtendWithDef") -> None:
        """Collect generic instantiations from perk implementation methods.

        Perk methods behave like extension methods - they return bare types (not Result<T>).
        This method collects generic instantiations from parameters and method bodies.
        """
        # Process each method in the perk implementation
        for method in perk_impl.methods:
            # Collect from return type (but don't wrap in Result)
            if method.ret is not None:
                self._collect_from_type(method.ret)

            # Collect from parameters
            for param in method.params:
                self._collect_from_param(param)

            # Collect from method body (variable declarations)
            self._collect_from_block(method.body)

    def _collect_from_param(self, param: Param) -> None:
        """Collect generic instantiations from parameter type."""
        if param.ty is not None:
            self._collect_from_type(param.ty)
            # Track parameter type for method call inference
            if param.name is not None:
                self.variable_types[param.name] = param.ty

    def _collect_from_block(self, block) -> None:
        """Collect generic instantiations from block statements."""
        for stmt in block.statements:
            self._collect_from_statement(stmt)

    def _collect_from_statement(self, stmt) -> None:
        """Collect generic instantiations from a statement."""
        # Import here to avoid circular dependency
        from semantics.ast import Let, Foreach, If, While, Match, Return, ExprStmt, Print, PrintLn, Rebind, Break, Continue

        if isinstance(stmt, Let):
            # Variable declaration with type annotation
            if stmt.ty is not None:
                self._collect_from_type(stmt.ty)
                # Track variable type for later reference
                if stmt.name is not None:
                    self.variable_types[stmt.name] = stmt.ty
            # NEW: Scan initialization expression
            if stmt.value is not None:
                self._collect_from_expression(stmt.value)

        elif isinstance(stmt, Foreach):
            # Foreach loop with type annotation
            if stmt.item_type is not None:
                self._collect_from_type(stmt.item_type)
            # NEW: Scan iterable expression
            if stmt.iterable is not None:
                self._collect_from_expression(stmt.iterable)
            # Also check body
            self._collect_from_block(stmt.body)

        elif isinstance(stmt, If):
            # If statement - check all arms and else block
            for cond, block in stmt.arms:
                # NEW: Scan condition expression
                self._collect_from_expression(cond)
                self._collect_from_block(block)
            if stmt.else_block is not None:
                self._collect_from_block(stmt.else_block)

        elif isinstance(stmt, While):
            # NEW: Scan condition expression
            if stmt.cond is not None:
                self._collect_from_expression(stmt.cond)
            # While statement - check body
            self._collect_from_block(stmt.body)

        elif isinstance(stmt, Match):
            # NEW: Scan scrutinee expression
            if stmt.scrutinee is not None:
                self._collect_from_expression(stmt.scrutinee)
            # Match statement - check all arm bodies
            for arm in stmt.arms:
                from semantics.ast import Block
                if isinstance(arm.body, Block):
                    self._collect_from_block(arm.body)
                # Note: If arm.body is an Expr, we don't need to collect types from it
                # because expressions don't introduce new type annotations

        elif isinstance(stmt, Return):
            # NEW: Scan return value expression
            if stmt.value is not None:
                self._collect_from_expression(stmt.value)

        elif isinstance(stmt, (ExprStmt, Print, PrintLn)):
            # NEW: Scan expression/value
            expr = stmt.expr if hasattr(stmt, 'expr') else stmt.value
            if expr is not None:
                self._collect_from_expression(expr)

        elif isinstance(stmt, Rebind):
            # NEW: Scan rebind value
            if stmt.value is not None:
                self._collect_from_expression(stmt.value)

        elif isinstance(stmt, (Break, Continue)):
            # These statements don't have expressions
            pass

    def _collect_from_expression(self, expr) -> None:
        """Recursively collect generic instantiations from expressions.

        This method scans all expression types to find DotCall nodes (receiver.method(args))
        on built-in types that return generic types (e.g., string.find() returning Maybe<i32>).

        Note: The parser creates DotCall nodes for all receiver.method(args) syntax.
        MethodCall nodes are only created internally in the backend, not in the parsed AST.

        We need to scan:
        1. DotCall expressions - the primary target for detecting generic returns
        2. All other expression types recursively to find nested DotCalls
        """
        # Import here to avoid circular dependency
        from semantics.ast import (
            Call, BinaryOp, UnaryOp, IndexAccess, ArrayLiteral,
            EnumConstructor, CastExpr, InterpolatedString, DotCall, TryExpr,
            IntLit, FloatLit, StringLit, BoolLit, Name, Borrow
        )

        if isinstance(expr, Call):
            # Regular function calls - check for generic function instantiation
            self._collect_from_call(expr)
            # Also scan arguments recursively
            for arg in expr.args:
                self._collect_from_expression(arg)

        elif isinstance(expr, DotCall):
            # Chained method calls (result.method())
            # DotCall can be either a method call or enum constructor
            # We treat it as a potential method call for generic return types
            self._collect_from_dot_call(expr)
            # Recursively scan receiver and arguments
            self._collect_from_expression(expr.receiver)
            for arg in expr.args:
                self._collect_from_expression(arg)

        elif isinstance(expr, BinaryOp):
            # Binary operations - scan both operands
            self._collect_from_expression(expr.left)
            self._collect_from_expression(expr.right)

        elif isinstance(expr, UnaryOp):
            # Unary operations - scan expression
            self._collect_from_expression(expr.expr)

        elif isinstance(expr, IndexAccess):
            # Array indexing - scan array and index
            self._collect_from_expression(expr.array)
            self._collect_from_expression(expr.index)

        elif isinstance(expr, ArrayLiteral):
            # Array literals - scan all elements
            for element in expr.elements:
                self._collect_from_expression(element)

        elif isinstance(expr, EnumConstructor):
            # Enum constructors - scan arguments
            for arg in expr.args:
                self._collect_from_expression(arg)

        elif isinstance(expr, CastExpr):
            # Type casts - scan the expression being cast
            self._collect_from_expression(expr.expr)

        elif isinstance(expr, InterpolatedString):
            # String interpolation - scan all parts
            for part in expr.parts:
                if not isinstance(part, str):  # Skip string literals
                    self._collect_from_expression(part)

        elif isinstance(expr, TryExpr):
            # Try operator (??) - scan the expression being unwrapped
            self._collect_from_expression(expr.expr)

        elif isinstance(expr, Borrow):
            # Borrow expression (&x) - scan the expression being borrowed
            self._collect_from_expression(expr.expr)

        elif isinstance(expr, (IntLit, FloatLit, StringLit, BoolLit, Name)):
            # Literals and names don't contain nested expressions
            pass

    def _collect_from_dot_call(self, call) -> None:
        """Detect built-in method calls (via DotCall) with generic return types.

        DotCall is used for all receiver.method(args) syntax before semantic analysis.
        It can be either an enum constructor (Result.Ok(42)) or a method call (arr.push(5)).

        For instantiation collection, we treat all DotCalls as potential method calls
        and check if they have generic return types.

        Strategy:
        1. Infer the receiver type (simple cases only - literals, known names)
        2. Look up the built-in method's return type
        3. If the return type is generic, record it for monomorphization
        """
        from semantics.ast import DotCall

        if not isinstance(call, DotCall):
            return

        # Infer receiver type (simple cases only - no full type inference needed)
        receiver_type = self._infer_simple_receiver_type(call.receiver)

        if receiver_type is not None:
            # Look up built-in method return type
            return_type = self._get_builtin_method_return_type(receiver_type, call.method)

            # If return type is generic, collect it
            if return_type is not None and isinstance(return_type, GenericTypeRef):
                self._collect_from_type(return_type)

    def _infer_simple_receiver_type(self, receiver) -> Type | None:
        """Simple type inference for method call receivers.

        We only need to handle simple cases (literals, builtins) since complex
        expressions can use explicit type annotations. This is sufficient for
        detecting calls like "hello".find() where the receiver is a string literal.

        Returns:
            The type of the receiver, or None if we can't infer it
        """
        from semantics.ast import Name, StringLit, IntLit, FloatLit, BoolLit, DotCall

        # Dispatch to type-specific handler based on receiver type
        if isinstance(receiver, StringLit):
            return self._infer_stringlit_type(receiver)
        elif isinstance(receiver, IntLit):
            return self._infer_intlit_type(receiver)
        elif isinstance(receiver, FloatLit):
            return self._infer_floatlit_type(receiver)
        elif isinstance(receiver, BoolLit):
            return self._infer_boollit_type(receiver)
        elif isinstance(receiver, DotCall):
            return self._infer_dotcall_type(receiver)
        elif isinstance(receiver, Name):
            return self._infer_name_type(receiver)
        else:
            # For more complex expressions, return None
            # Users will need to use explicit type annotations
            return None

    def _infer_stringlit_type(self, expr) -> Type:
        """Infer type for string literal."""
        from semantics.typesys import BuiltinType
        return BuiltinType.STRING

    def _infer_intlit_type(self, expr) -> Type:
        """Infer type for integer literal (for future int extension methods)."""
        from semantics.typesys import BuiltinType
        return BuiltinType.I32

    def _infer_floatlit_type(self, expr) -> Type:
        """Infer type for float literal (for future float extension methods)."""
        from semantics.typesys import BuiltinType
        return BuiltinType.F64

    def _infer_boollit_type(self, expr) -> Type:
        """Infer type for bool literal (for future bool extension methods)."""
        from semantics.typesys import BuiltinType
        return BuiltinType.BOOL

    def _infer_dotcall_type(self, expr) -> Type | None:
        """Infer type for chained method call expressions.

        This handles chained calls like text.find("x").realise(-1) by
        recursively inferring the receiver type and looking up the method's
        return type.
        """
        # Recursively infer the receiver type
        inner_receiver_type = self._infer_simple_receiver_type(expr.receiver)
        if inner_receiver_type is not None:
            # Look up the method's return type
            return self._get_builtin_method_return_type(inner_receiver_type, expr.method)
        return None

    def _infer_name_type(self, expr) -> Type | None:
        """Infer type for name references (variables, builtins).

        Handles:
        - Builtin I/O streams (stdin, stdout, stderr)
        - Known variables with explicit type annotations
        """
        from semantics.typesys import BuiltinType

        # Handle builtin I/O streams
        if expr.id == "stdin":
            return BuiltinType.STDIN
        elif expr.id == "stdout":
            return BuiltinType.STDOUT
        elif expr.id == "stderr":
            return BuiltinType.STDERR

        # Check if this is a known variable with explicit type annotation
        if expr.id in self.variable_types:
            return self.variable_types[expr.id]

        # For other variables, we would need full type inference which we don't
        # have access to at this stage. Users can use explicit type annotations
        # for complex cases.
        return None

    def _get_builtin_method_return_type(self, receiver_type: Type, method_name: str) -> Type | None:
        """Get return type of built-in extension methods.

        Returns both generic and non-generic return types to support chained
        method calls (e.g., text.find().realise(-1)).

        Returns:
            The return type of the method, or None if unknown
        """
        from semantics.typesys import BuiltinType

        # String methods
        if receiver_type == BuiltinType.STRING:
            if method_name == "find":
                # string.find() returns Maybe<i32>
                return GenericTypeRef(base_name="Maybe", type_args=(BuiltinType.I32,))
            elif method_name == "upper" or method_name == "lower" or method_name == "cap" or method_name == "trim" or method_name == "tleft" or method_name == "tright":
                return BuiltinType.STRING
            # Other string methods - skip for now

        # Maybe<T> methods
        if isinstance(receiver_type, GenericTypeRef) and receiver_type.base_name == "Maybe":
            if method_name == "realise" or method_name == "expect":
                # Maybe<T>.realise(T) -> T, Maybe<T>.expect(string) -> T
                # Extract T from Maybe<T>
                if receiver_type.type_args:
                    return receiver_type.type_args[0]
            elif method_name == "is_some" or method_name == "is_none":
                return BuiltinType.BOOL

        # Future extension points:
        # - Array methods: array.get() could return Maybe<T>
        # - HashMap methods: map.get() could return Maybe<V>
        # - Result methods: result.and_then() could take generic closures

        return None

    def _infer_simple_expr_type(self, expr) -> Type | None:
        """Infer type of simple expression for generic function type inference.

        This is a best-effort inference for common cases:
        - Literals (integers, strings, booleans, floats)
        - Struct constructors
        - Enum constructors
        - Cast expressions

        Returns:
            Inferred type or None if can't infer
        """
        from semantics.ast import IntLit, FloatLit, StringLit, BoolLit, Name, EnumConstructor, CastExpr
        from semantics.typesys import BuiltinType

        # Integer literal
        if isinstance(expr, IntLit):
            return BuiltinType.I32

        # Float literal
        if isinstance(expr, FloatLit):
            return BuiltinType.F64

        # String literal
        if isinstance(expr, StringLit):
            return BuiltinType.STRING

        # Boolean literal
        if isinstance(expr, BoolLit):
            return BuiltinType.BOOL

        # Cast expression - return the target type
        if isinstance(expr, CastExpr):
            return expr.target_type

        # Variable name - look up in variable_types dict
        if isinstance(expr, Name):
            var_name = expr.id
            if var_name in self.variable_types:
                var_type = self.variable_types[var_name]
                # Resolve UnknownType to concrete type if possible
                resolved = resolve_unknown_type(var_type, self.struct_table, self.enum_table)
                return resolved if resolved is not None else var_type
            return None

        # Enum constructor (e.g., Result.Ok(42), Maybe.Some(5))
        if isinstance(expr, EnumConstructor):
            enum_name = expr.enum_name
            if self.enum_table and enum_name in self.enum_table:
                return self.enum_table[enum_name]
            return None

        # For complex expressions, we can't infer without full type checking
        return None

    def _unify_types(self, param_type: Type, arg_type: Type, type_param_map: dict[str, Type]) -> bool:
        """Unify parameter type with argument type.

        Extracts type parameter assignments during unification.

        Args:
            param_type: Parameter type (may contain TypeParameter)
            arg_type: Argument type (concrete type)
            type_param_map: Accumulator for type parameter assignments

        Returns:
            True if unification succeeds, False otherwise
        """
        from semantics.generics.types import TypeParameter

        # Case 1: param_type is a type parameter
        if isinstance(param_type, TypeParameter):
            param_name = param_type.name

            # Check if already assigned
            if param_name in type_param_map:
                # Must match existing assignment
                return type_param_map[param_name] == arg_type
            else:
                # New assignment
                type_param_map[param_name] = arg_type
                return True

        # Case 1b: param_type is UnknownType (may be type parameter name)
        # This happens when generic function parameters are parsed as UnknownType("T")
        from semantics.typesys import UnknownType
        if isinstance(param_type, UnknownType):
            param_name = str(param_type)

            # Check if already assigned
            if param_name in type_param_map:
                # Must match existing assignment
                return type_param_map[param_name] == arg_type
            else:
                # New assignment
                type_param_map[param_name] = arg_type
                return True

        # Case 2: Both are concrete types - must match exactly
        if param_type == arg_type:
            return True

        # Case 3: Nested generic types (e.g., Container<T>)
        # Handle GenericTypeRef unified with concrete monomorphized type
        from semantics.generics.types import GenericTypeRef
        from semantics.typesys import StructType, EnumType

        if isinstance(param_type, GenericTypeRef):
            param_base = param_type.base_name
            param_type_args = param_type.type_args

            # Check if arg_type is a monomorphized generic with metadata
            if isinstance(arg_type, (StructType, EnumType)):
                # Use generic metadata if available
                if arg_type.generic_base is not None and arg_type.generic_args is not None:
                    # Check base names match
                    if param_base != arg_type.generic_base:
                        return False

                    # Check type argument counts match
                    if len(param_type_args) != len(arg_type.generic_args):
                        return False

                    # Recursively unify each type argument
                    for param_arg, concrete_arg in zip(param_type_args, arg_type.generic_args):
                        if not self._unify_types(param_arg, concrete_arg, type_param_map):
                            return False

                    return True

            # If arg_type is also a GenericTypeRef, unify them directly
            elif isinstance(arg_type, GenericTypeRef):
                arg_base = arg_type.base_name
                arg_type_args = arg_type.type_args

                # Base names must match
                if param_base != arg_base:
                    return False

                # Type argument counts must match
                if len(param_type_args) != len(arg_type_args):
                    return False

                # Recursively unify each type argument pair
                for param_arg, arg_arg in zip(param_type_args, arg_type_args):
                    if not self._unify_types(param_arg, arg_arg, type_param_map):
                        return False

                return True

        return False

    def _infer_type_args_from_call(self, call, generic_func) -> tuple[Type, ...] | None:
        """Infer type arguments for generic function call.

        Uses simple unification to match argument types to parameter types.

        Args:
            call: Call AST node
            generic_func: Generic function definition

        Returns:
            Tuple of concrete types for type parameters, or None if inference fails
        """
        import sys
        # Build type parameter -> concrete type mapping
        type_param_map: dict[str, Type] = {}

        # Check argument count matches parameter count
        call_args = getattr(call, "args", []) or []
        func_params = generic_func.params

        if len(call_args) != len(func_params):
            # Argument count mismatch - can't infer
            return None

        # Match each argument to corresponding parameter
        for i, (arg_expr, param) in enumerate(zip(call_args, func_params)):
            # Infer argument type
            arg_type = self._infer_simple_expr_type(arg_expr)
            if arg_type is None:
                # Can't infer argument type
                return None

            # Unify argument type with parameter type
            if param.ty is None:
                # Parameter has no type annotation - shouldn't happen
                return None

            success = self._unify_types(param.ty, arg_type, type_param_map)
            if not success:
                # Unification failed
                return None

        # Check that all type parameters were inferred
        for tp in generic_func.type_params:
            tp_name = tp.name if hasattr(tp, 'name') else str(tp)
            if tp_name not in type_param_map:
                # Type parameter not inferred (not used in parameters)
                return None

        # Extract type arguments in parameter order and resolve UnknownType
        from semantics.type_resolution import resolve_unknown_type
        type_args = []
        for tp in generic_func.type_params:
            tp_name = tp.name if hasattr(tp, 'name') else str(tp)
            inferred_type = type_param_map[tp_name]
            # Resolve UnknownType to concrete StructType/EnumType if possible
            resolved_type = resolve_unknown_type(inferred_type, self.struct_table or {}, self.enum_table or {})
            type_args.append(resolved_type)

        return tuple(type_args)

    def _collect_from_call(self, call) -> None:
        """Detect generic function calls and infer type arguments.

        Args:
            call: Call AST node
        """
        from semantics.ast import Name

        # Get function name
        callee = getattr(call, "callee", None)
        if not isinstance(callee, Name):
            # Not a simple function call (could be complex expression)
            return

        function_name = callee.id

        # Check if this is a generic function
        if not self.generic_funcs or function_name not in self.generic_funcs:
            # Not a generic function
            return

        generic_func = self.generic_funcs[function_name]

        # Infer type arguments from call site
        type_args = self._infer_type_args_from_call(call, generic_func)

        if type_args is not None:
            # Successfully inferred - record instantiation
            self.function_instantiations.add((function_name, type_args))

            # IMPORTANT: Also detect Result<T> instantiation for the return type
            # All Sushi functions implicitly return Result<T> where T is the declared return type
            if generic_func.ret is not None:
                # Substitute type parameters in return type
                ret_type = self._substitute_type_simple(generic_func.ret, generic_func.type_params, type_args)
                # Add Result<ret_type> to enum instantiations
                if ret_type is not None:
                    self.instantiations.add(("Result", (ret_type,)))

            # Note: We don't emit errors here if inference fails
            # Type validation will catch that in Pass 2

    def _substitute_type_simple(self, ty: Type, type_params: tuple, type_args: tuple) -> Type:
        """Simple type substitution for instantiation detection.

        Args:
            ty: Type that may contain type parameters
            type_params: Type parameter definitions (BoundedTypeParam)
            type_args: Concrete type arguments

        Returns:
            Type with type parameters substituted
        """
        from semantics.generics.types import TypeParameter
        from semantics.typesys import UnknownType

        # Build substitution map
        substitution = {}
        for param, arg in zip(type_params, type_args):
            param_name = param.name if hasattr(param, 'name') else str(param)
            substitution[param_name] = arg

        # If type is a type parameter, substitute it
        if isinstance(ty, TypeParameter):
            param_name = ty.name
            if param_name in substitution:
                return substitution[param_name]
            return ty

        # If type is UnknownType that represents a type parameter
        if isinstance(ty, UnknownType):
            type_name = str(ty)
            if type_name in substitution:
                return substitution[type_name]
            return ty

        # Otherwise return as-is (concrete types like i32, structs, etc.)
        return ty

    def _resolve_unknown_type(self, ty: Type) -> Type:
        """Resolve UnknownType to StructType or EnumType if possible.

        Delegates to the centralized type_resolution module.
        """
        # Use centralized resolution function
        return resolve_unknown_type(ty, self.struct_table or {}, self.enum_table or {})

    def _contains_unresolvable_unknown_type_in_tuple(self, type_args: tuple[Type, ...]) -> bool:
        """Check if any type argument tuple contains UnknownType that cannot be resolved.

        This is a wrapper around the centralized contains_unresolvable_unknown_type
        that handles tuple iteration.
        """
        for arg in type_args:
            if contains_unresolvable_unknown_type(arg, self.struct_table or {}, self.enum_table or {}):
                return True
        return False

    def _resolve_type_args(self, type_args: tuple[Type, ...]) -> tuple[Type, ...]:
        """Resolve all UnknownType instances in type_args to StructType or EnumType if possible."""
        from semantics.typesys import ArrayType, DynamicArrayType

        resolved_args = []
        for arg in type_args:
            resolved_arg = self._resolve_unknown_type(arg)

            # Recursively resolve nested types
            if isinstance(resolved_arg, (ArrayType, DynamicArrayType)):
                resolved_base = self._resolve_unknown_type(resolved_arg.base_type)
                if isinstance(resolved_arg, ArrayType):
                    resolved_arg = ArrayType(base_type=resolved_base, size=resolved_arg.size)
                else:
                    resolved_arg = DynamicArrayType(base_type=resolved_base)
            elif isinstance(resolved_arg, GenericTypeRef):
                resolved_nested_args = self._resolve_type_args(resolved_arg.type_args)
                resolved_arg = GenericTypeRef(base_name=resolved_arg.base_name, type_args=resolved_nested_args)

            resolved_args.append(resolved_arg)

        return tuple(resolved_args)

    def _collect_from_type(self, ty: Type) -> None:
        """Collect generic instantiations from a type annotation.

        This is the core method that detects GenericTypeRef instances and
        records them in the instantiations set.
        """
        if isinstance(ty, GenericTypeRef):
            # Found a generic type instantiation!
            # Try to resolve any UnknownType instances to StructType or EnumType
            resolved_type_args = self._resolve_type_args(ty.type_args)

            # Skip if any type argument is still UnknownType (can't be resolved)
            if self._contains_unresolvable_unknown_type_in_tuple(resolved_type_args):
                return

            # Record it as (base_name, type_args) tuple with resolved types
            self.instantiations.add((ty.base_name, resolved_type_args))

            # Recursively collect from type arguments
            # Example: Result<Result<i32>> has nested generics
            for arg in resolved_type_args:
                self._collect_from_type(arg)

        # For array types, check element type
        from semantics.typesys import ArrayType, DynamicArrayType
        if isinstance(ty, ArrayType):
            self._collect_from_type(ty.base_type)
        elif isinstance(ty, DynamicArrayType):
            self._collect_from_type(ty.base_type)

        # For struct types, check field types
        from semantics.typesys import StructType
        if isinstance(ty, StructType):
            # Check for cycles to prevent infinite recursion on recursive structs
            # (e.g., struct Node with Own<Node> field)
            type_key = f"struct:{ty.name}"
            if type_key in self.visited_types:
                return  # Already processed this struct

            self.visited_types.add(type_key)

            for field_name, field_type in ty.fields:
                self._collect_from_type(field_type)

        # For enum types, check variant associated types
        from semantics.typesys import EnumType
        if isinstance(ty, EnumType):
            # Check for cycles to prevent infinite recursion on recursive enums
            type_key = f"enum:{ty.name}"
            if type_key in self.visited_types:
                return  # Already processed this enum

            self.visited_types.add(type_key)

            for variant in ty.variants:
                for assoc_type in variant.associated_types:
                    self._collect_from_type(assoc_type)

            self.visited_types.discard(type_key)  # Allow revisiting from different paths
