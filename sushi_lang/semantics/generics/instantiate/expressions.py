# semantics/generics/instantiate/expressions.py
"""
Expression scanning for instantiation collection.

Recursively traverses expression AST nodes to detect generic type instantiations
from method calls, function calls, and nested expressions.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Set, Tuple

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.generics.instantiate.types import TypeInferrer

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.type_resolution import TypeResolver


class ExpressionScanner:
    """Scans expressions to collect generic type instantiations.

    Handles:
    - Function calls (including generic functions)
    - Method calls (DotCall nodes)
    - Nested expressions
    """

    def __init__(
        self,
        type_inferrer: "TypeInferrer",
        instantiations: Set[Tuple[str, Tuple["Type", ...]]],
        function_instantiations: Set[Tuple[str, Tuple["Type", ...]]],
        generic_funcs: dict,
    ):
        """Initialize expression scanner.

        Args:
            type_inferrer: Type inference helper
            instantiations: Set to accumulate type instantiations
            function_instantiations: Set to accumulate function instantiations
            generic_funcs: Table of generic function definitions
        """
        self.type_inferrer = type_inferrer
        self.instantiations = instantiations
        self.function_instantiations = function_instantiations
        self.generic_funcs = generic_funcs
        # Create TypeResolver for centralized type resolution
        self._resolver = TypeResolver(
            type_inferrer.struct_table or {},
            type_inferrer.enum_table or {}
        )

    def scan_expression(self, expr) -> None:
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
        from sushi_lang.semantics.ast import (
            Call, BinaryOp, UnaryOp, IndexAccess, ArrayLiteral,
            EnumConstructor, CastExpr, InterpolatedString, DotCall, TryExpr,
            IntLit, FloatLit, StringLit, BoolLit, Name, Borrow
        )

        if isinstance(expr, Call):
            # Regular function calls - check for generic function instantiation
            self._scan_call(expr)
            # Also scan arguments recursively
            for arg in expr.args:
                self.scan_expression(arg)

        elif isinstance(expr, DotCall):
            # Chained method calls (result.method())
            # DotCall can be either a method call or enum constructor
            # We treat it as a potential method call for generic return types
            self._scan_dot_call(expr)
            # Recursively scan receiver and arguments
            self.scan_expression(expr.receiver)
            for arg in expr.args:
                self.scan_expression(arg)

        elif isinstance(expr, BinaryOp):
            # Binary operations - scan both operands
            self.scan_expression(expr.left)
            self.scan_expression(expr.right)

        elif isinstance(expr, UnaryOp):
            # Unary operations - scan expression
            self.scan_expression(expr.expr)

        elif isinstance(expr, IndexAccess):
            # Array indexing - scan array and index
            self.scan_expression(expr.array)
            self.scan_expression(expr.index)

        elif isinstance(expr, ArrayLiteral):
            # Array literals - scan all elements
            for element in expr.elements:
                self.scan_expression(element)

        elif isinstance(expr, EnumConstructor):
            # Enum constructors - scan arguments
            for arg in expr.args:
                self.scan_expression(arg)

        elif isinstance(expr, CastExpr):
            # Type casts - scan the expression being cast
            self.scan_expression(expr.expr)

        elif isinstance(expr, InterpolatedString):
            # String interpolation - scan all parts
            for part in expr.parts:
                if not isinstance(part, str):  # Skip string literals
                    self.scan_expression(part)

        elif isinstance(expr, TryExpr):
            # Try operator (??) - scan the expression being unwrapped
            self.scan_expression(expr.expr)

        elif isinstance(expr, Borrow):
            # Borrow expression (&x) - scan the expression being borrowed
            self.scan_expression(expr.expr)

        elif isinstance(expr, (IntLit, FloatLit, StringLit, BoolLit, Name)):
            # Literals and names don't contain nested expressions
            pass

    def _scan_dot_call(self, call) -> None:
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
        from sushi_lang.semantics.ast import DotCall

        if not isinstance(call, DotCall):
            return

        # Infer receiver type (simple cases only - no full type inference needed)
        receiver_type = self.type_inferrer.infer_simple_receiver_type(call.receiver)

        if receiver_type is not None:
            # Look up built-in method return type
            return_type = self.type_inferrer.get_builtin_method_return_type(receiver_type, call.method)

            # If return type is generic, collect it
            if return_type is not None and isinstance(return_type, GenericTypeRef):
                self._collect_from_type(return_type)

    def _scan_call(self, call) -> None:
        """Detect generic function calls and infer type arguments.

        Args:
            call: Call AST node
        """
        from sushi_lang.semantics.ast import Name
        from sushi_lang.semantics.typesys import BuiltinType

        # Get function name
        callee = getattr(call, "callee", None)
        if not isinstance(callee, Name):
            # Not a simple function call (could be complex expression)
            return

        function_name = callee.id

        # Check for stdlib functions that return generic types
        if function_name in {'sleep', 'msleep', 'usleep', 'nanosleep'}:
            # Time functions return Result<i32, StdError>
            std_error = self.type_inferrer.enum_table.get("StdError")
            if std_error:
                self.instantiations.add(("Result", (BuiltinType.I32, std_error)))
            return
        elif function_name == 'setenv':
            # setenv returns Result<i32, EnvError>
            env_error = self.type_inferrer.enum_table.get("EnvError")
            if env_error:
                self.instantiations.add(("Result", (BuiltinType.I32, env_error)))
            return
        elif function_name == 'getenv':
            # getenv() returns Maybe<string>
            self.instantiations.add(("Maybe", (BuiltinType.STRING,)))
            return
        elif function_name == 'file_size':
            # file_size() returns Result<i64, FileError>
            file_error = self.type_inferrer.enum_table.get("FileError")
            if file_error:
                self.instantiations.add(("Result", (BuiltinType.I64, file_error)))
            return
        elif function_name in {'remove', 'rename', 'copy', 'mkdir', 'rmdir'}:
            # File utility functions return Result<i32, FileError>
            file_error = self.type_inferrer.enum_table.get("FileError")
            if file_error:
                self.instantiations.add(("Result", (BuiltinType.I32, file_error)))
            return

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

            # IMPORTANT: Also detect Result<T, E> instantiation for the return type
            # All Sushi functions implicitly return Result<T, E> where T is the declared return type
            # and E is StdError by default (unless explicitly specified)
            if generic_func.ret is not None:
                # Substitute type parameters in return type
                ret_type = self.type_inferrer.substitute_type_simple(
                    generic_func.ret, generic_func.type_params, type_args
                )
                # Add Result<ret_type, StdError> to enum instantiations
                if ret_type is not None:
                    std_error = self.type_inferrer.enum_table.get("StdError")
                    if std_error:
                        self.instantiations.add(("Result", (ret_type, std_error)))

            # Note: We don't emit errors here if inference fails
            # Type validation will catch that in Pass 2

    def _infer_type_args_from_call(self, call, generic_func) -> tuple["Type", ...] | None:
        """Infer type arguments for generic function call.

        Uses simple unification to match argument types to parameter types.

        Args:
            call: Call AST node
            generic_func: Generic function definition

        Returns:
            Tuple of concrete types for type parameters, or None if inference fails
        """
        from sushi_lang.semantics.type_resolution import resolve_unknown_type

        # Build type parameter -> concrete type mapping
        type_param_map: dict[str, "Type"] = {}

        # Check argument count matches parameter count
        call_args = getattr(call, "args", []) or []
        func_params = generic_func.params

        if len(call_args) != len(func_params):
            # Argument count mismatch - can't infer
            return None

        # Match each argument to corresponding parameter
        for i, (arg_expr, param) in enumerate(zip(call_args, func_params)):
            # Infer argument type
            arg_type = self.type_inferrer.infer_simple_expr_type(arg_expr)
            if arg_type is None:
                # Can't infer argument type
                return None

            # Unify argument type with parameter type
            if param.ty is None:
                # Parameter has no type annotation - shouldn't happen
                return None

            success = self.type_inferrer.unify_types(param.ty, arg_type, type_param_map)
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
        type_args = []
        for tp in generic_func.type_params:
            tp_name = tp.name if hasattr(tp, 'name') else str(tp)
            inferred_type = type_param_map[tp_name]
            # Resolve UnknownType to concrete StructType/EnumType if possible
            resolved_type = resolve_unknown_type(
                inferred_type,
                self.type_inferrer.struct_table or {},
                self.type_inferrer.enum_table or {}
            )
            type_args.append(resolved_type)

        return tuple(type_args)

    def _collect_from_type(self, ty: "Type") -> None:
        """Collect generic instantiations from a type annotation.

        This is a helper that delegates to the main collector's type collection logic.
        """
        if isinstance(ty, GenericTypeRef):
            # Found a generic type instantiation!
            # Use TypeResolver for centralized resolution
            resolved_type_args = self._resolver.resolve_type_args(ty.type_args)

            # Skip if any type argument is still UnknownType (can't be resolved)
            if self._resolver.contains_unresolvable_in_tuple(resolved_type_args):
                return

            # Record it as (base_name, type_args) tuple with resolved types
            self.instantiations.add((ty.base_name, resolved_type_args))

            # Recursively collect from type arguments
            # Example: Result<Result<i32>> has nested generics
            for arg in resolved_type_args:
                self._collect_from_type(arg)
