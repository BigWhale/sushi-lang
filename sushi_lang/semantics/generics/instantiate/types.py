# semantics/generics/instantiate/types.py
"""
Type inference for instantiation collection.

Handles simple type inference for literals, variables, and built-in types
to support automatic generic type instantiation detection.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type

from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter
from sushi_lang.semantics.type_resolution import resolve_unknown_type


class TypeInferrer:
    """Simple type inference for instantiation collection.

    Handles common cases like literals, built-in types, and simple expressions
    without requiring full type checking infrastructure.
    """

    def __init__(self, variable_types: dict[str, "Type"], struct_table: dict, enum_table: dict,
                 func_table: dict | None = None):
        """Initialize type inferrer.

        Args:
            variable_types: Map of variable names to their declared types
            struct_table: Table of struct definitions
            enum_table: Table of enum definitions
            func_table: Table of plain top-level functions (name -> FuncSig), for
                presenting a FunctionType for a bare function reference argument
        """
        self.variable_types = variable_types
        self.struct_table = struct_table
        self.enum_table = enum_table
        self.func_table = func_table or {}

    def infer_simple_receiver_type(self, receiver) -> "Type | None":
        """Simple type inference for method call receivers.

        We only need to handle simple cases (literals, builtins) since complex
        expressions can use explicit type annotations. This is sufficient for
        detecting calls like "hello".find() where the receiver is a string literal.

        Returns:
            The type of the receiver, or None if we can't infer it
        """
        from sushi_lang.semantics.ast import Name, StringLit, IntLit, FloatLit, BoolLit, DotCall

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

    def _infer_stringlit_type(self, expr) -> "Type":
        """Infer type for string literal."""
        return BuiltinType.STRING

    def _infer_intlit_type(self, expr) -> "Type":
        """Infer type for integer literal (for future int extension methods)."""
        return BuiltinType.I32

    def _infer_floatlit_type(self, expr) -> "Type":
        """Infer type for float literal (for future float extension methods)."""
        return BuiltinType.F64

    def _infer_boollit_type(self, expr) -> "Type":
        """Infer type for bool literal (for future bool extension methods)."""
        return BuiltinType.BOOL

    def _infer_dotcall_type(self, expr) -> "Type | None":
        """Infer type for chained method call expressions.

        This handles chained calls like text.find("x").realise(-1) by
        recursively inferring the receiver type and looking up the method's
        return type.
        """
        # Recursively infer the receiver type
        inner_receiver_type = self.infer_simple_receiver_type(expr.receiver)
        if inner_receiver_type is not None:
            # Look up the method's return type
            return self.get_builtin_method_return_type(inner_receiver_type, expr.method)
        return None

    def _infer_name_type(self, expr) -> "Type | None":
        """Infer type for name references (variables, builtins).

        Handles:
        - Builtin I/O streams (stdin, stdout, stderr)
        - Known variables with explicit type annotations
        """
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

    def get_builtin_method_return_type(self, receiver_type: "Type", method_name: str) -> "Type | None":
        """Get return type of built-in extension methods.

        Returns both generic and non-generic return types to support chained
        method calls (e.g., text.find().realise(-1)).

        Returns:
            The return type of the method, or None if unknown
        """
        # String methods
        if receiver_type == BuiltinType.STRING:
            if method_name in ("find", "find_last"):
                # string.find()/find_last() returns Maybe<i32>
                return GenericTypeRef(base_name="Maybe", type_args=(BuiltinType.I32,))
            elif method_name in ("upper", "lower", "cap", "trim", "tleft", "tright"):
                return BuiltinType.STRING
            # Other string methods - skip for now

        # Maybe<T> methods
        if isinstance(receiver_type, GenericTypeRef) and receiver_type.base_name == "Maybe":
            if method_name in ("realise", "expect"):
                # Maybe<T>.realise(T) -> T, Maybe<T>.expect(string) -> T
                # Extract T from Maybe<T>
                if receiver_type.type_args:
                    return receiver_type.type_args[0]
            elif method_name in ("is_some", "is_none"):
                return BuiltinType.BOOL

        # Future extension points:
        # - Array methods: array.get() could return Maybe<T>
        # - HashMap methods: map.get() could return Maybe<V>
        # - Result methods: result.and_then() could take generic closures

        return None

    def infer_simple_expr_type(self, expr) -> "Type | None":
        """Infer type of simple expression for generic function type inference.

        This is a best-effort inference for common cases:
        - Literals (integers, strings, booleans, floats)
        - Struct constructors
        - Enum constructors
        - Cast expressions

        Returns:
            Inferred type or None if can't infer
        """
        from sushi_lang.semantics.ast import (
            IntLit, FloatLit, StringLit, BoolLit, Name, EnumConstructor, CastExpr, DotCall,
            Lambda, BinaryOp,
        )

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
            # A bare reference to a plain top-level function used as a higher-order
            # argument: present its FunctionType (mirrors function_value_type_of).
            sig = self.func_table.get(var_name) if self.func_table else None
            if sig is not None:
                from sushi_lang.semantics.typesys import FunctionType, UnknownType
                params = getattr(sig, "params", None)
                if params is not None and not any(
                    getattr(p, "is_variadic", False) or getattr(p, "is_pack", False) for p in params
                ):
                    param_types = tuple(getattr(p, "ty", None) for p in params)
                    if not any(pt is None for pt in param_types):
                        ok_type = getattr(sig, "ret_type", None)
                        if ok_type is not None:
                            err_type = getattr(sig, "err_type", None) or UnknownType("StdError")
                            return FunctionType(param_types=param_types, ok_type=ok_type,
                                                err_type=err_type)
            return None

        # Enum constructor (e.g., Result.Ok(42), Maybe.Some(5))
        if isinstance(expr, EnumConstructor):
            enum_name = expr.enum_name
            if self.enum_table and enum_name in self.enum_table:
                return self.enum_table[enum_name]
            return None

        # Dot-call (e.g., Color.Red(), Point.new()): the pre-lowering form of an
        # enum/struct constructor. `X.Y(args)` parses to a DotCall and is only
        # rewritten to EnumConstructor later in analysis, so at Pass 1.5 the
        # constructor still looks like this. Resolving the receiver to its
        # enum/struct type here keeps Pass 1.5 in agreement with Pass 2's full
        # inferrer (which already resolves the DotCall); otherwise the pack/generic
        # instantiation is never registered and Pass 2 raises CE2061. The variant/
        # method name is intentionally NOT validated here -- Pass 2 does that.
        if isinstance(expr, DotCall):
            receiver = expr.receiver
            if isinstance(receiver, Name):
                if self.enum_table and receiver.id in self.enum_table:
                    return self.enum_table[receiver.id]
                if self.struct_table and receiver.id in self.struct_table:
                    return self.struct_table[receiver.id]
            return None

        # Binary operation: comparison/logical ops are bool; arithmetic/bitwise ops take
        # the operand type. Lets a lambda body like `x * 2` or `x > 3` be inferred so a
        # function-typed argument's return type is known at instantiation collection.
        if isinstance(expr, BinaryOp):
            if expr.op in ("==", "!=", "<", "<=", ">", ">=", "and", "or", "xor"):
                return BuiltinType.BOOL
            left = self.infer_simple_expr_type(expr.left)
            return left if left is not None else self.infer_simple_expr_type(expr.right)

        # Lambda: present the FunctionType so type params nested in a function-typed
        # argument (fn(T) -> U) can be inferred. Only TYPED params are supported here --
        # a bare-param lambda's types come from expected-type propagation (Pass 2), which
        # is not available at instantiation collection, so it returns None (best-effort).
        if isinstance(expr, Lambda):
            from sushi_lang.semantics.typesys import FunctionType, UnknownType
            param_types = tuple(p.ty for p in expr.params)
            if any(pt is None for pt in param_types):
                return None
            # ok_type: an explicit `-> T` annotation wins; otherwise infer an
            # expression body with the lambda params in scope.
            ok_type = expr.ret
            if ok_type is None and not expr.is_block_body:
                saved = self.variable_types
                self.variable_types = dict(saved)
                for p in expr.params:
                    if p.ty is not None:
                        self.variable_types[p.name] = p.ty
                ok_type = self.infer_simple_expr_type(expr.body)
                self.variable_types = saved
            if ok_type is None:
                return None
            return FunctionType(param_types=param_types, ok_type=ok_type,
                                err_type=UnknownType("StdError"))

        # For complex expressions, we can't infer without full type checking
        return None

    def unify_types(self, param_type: "Type", arg_type: "Type", type_param_map: dict[str, "Type"]) -> bool:
        """Unify parameter type with argument type.

        Extracts type parameter assignments during unification.

        Args:
            param_type: Parameter type (may contain TypeParameter)
            arg_type: Argument type (concrete type)
            type_param_map: Accumulator for type parameter assignments

        Returns:
            True if unification succeeds, False otherwise
        """
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
        from sushi_lang.semantics.typesys import UnknownType
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
        from sushi_lang.semantics.typesys import StructType, EnumType

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
                        if not self.unify_types(param_arg, concrete_arg, type_param_map):
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
                    if not self.unify_types(param_arg, arg_arg, type_param_map):
                        return False

                return True

        # Case 4: Function types (fn(T) -> U). Twin of _unify_types_for_inference
        # (Pass 2): unify each parameter type and the return type so type params nested
        # inside a function-typed argument are inferred at instantiation collection.
        from sushi_lang.semantics.typesys import FunctionType
        if isinstance(param_type, FunctionType) and isinstance(arg_type, FunctionType):
            if len(param_type.param_types) != len(arg_type.param_types):
                return False
            for p_param, a_param in zip(param_type.param_types, arg_type.param_types):
                if not self.unify_types(p_param, a_param, type_param_map):
                    return False
            return self.unify_types(param_type.ok_type, arg_type.ok_type, type_param_map)

        return False

    def substitute_type_simple(self, ty: "Type", type_params: tuple, type_args: tuple) -> "Type":
        """Simple type substitution for instantiation detection.

        Args:
            ty: Type that may contain type parameters
            type_params: Type parameter definitions (BoundedTypeParam)
            type_args: Concrete type arguments

        Returns:
            Type with type parameters substituted
        """
        from sushi_lang.semantics.typesys import UnknownType

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
