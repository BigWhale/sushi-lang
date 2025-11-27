# semantics/generics/instantiate/functions.py
"""
Function-level instantiation collection.

Handles collecting generic type instantiations from function signatures,
extension methods, perk implementations, and statement-level type annotations.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Set, Tuple

if TYPE_CHECKING:
    from semantics.typesys import Type
    from semantics.generics.instantiate.expressions import ExpressionScanner

from semantics.generics.types import GenericTypeRef


class FunctionCollector:
    """Collects generic instantiations from function-level constructs.

    Handles:
    - Function signatures (parameters, return types)
    - Extension method signatures
    - Perk implementation methods
    - Statement blocks
    """

    def __init__(
        self,
        expression_scanner: "ExpressionScanner",
        instantiations: Set[Tuple[str, Tuple["Type", ...]]],
        variable_types: dict[str, "Type"],
        visited_types: Set[str],
    ):
        """Initialize function collector.

        Args:
            expression_scanner: Expression scanner for nested expressions
            instantiations: Set to accumulate type instantiations
            variable_types: Map of variable names to their types (for tracking)
            visited_types: Set of visited type keys (for cycle detection)
        """
        self.expression_scanner = expression_scanner
        self.instantiations = instantiations
        self.variable_types = variable_types
        self.visited_types = visited_types

    def collect_from_function(self, func) -> None:
        """Collect generic instantiations from function signature and body."""
        # Skip generic functions entirely - they will be scanned during monomorphization
        if hasattr(func, 'type_params') and func.type_params:
            return

        # Collect from return type
        # IMPORTANT: All functions implicitly return Result<T, E>, so we need to
        # record Result<T, E> instantiation for the function's return type
        if func.ret is not None:
            self._collect_from_type(func.ret)
            # If func.ret is not already a Result type, add implicit Result<T, StdError> instantiation
            from semantics.typesys import GenericTypeRef as GTypeRef, UnknownType
            if not (isinstance(func.ret, GTypeRef) and func.ret.base_name == "Result"):
                # Add implicit Result<T, StdError> instantiation for functions without explicit Result
                # StdError is a predefined EnumType, reference it by name using UnknownType
                std_error_ref = UnknownType("StdError")
                result_instantiation = GenericTypeRef(base_name="Result", type_args=(func.ret, std_error_ref))
                self._collect_from_type(result_instantiation)

        # Collect from parameters
        for param in func.params:
            self._collect_from_param(param)

        # Collect from function body (variable declarations)
        self._collect_from_block(func.body)

    def collect_from_extension(self, ext) -> None:
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

    def collect_from_perk_impl(self, perk_impl) -> None:
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

    def collect_from_const(self, const) -> None:
        """Collect generic instantiations from constant definition."""
        if const.ty is not None:
            self._collect_from_type(const.ty)

    def collect_from_struct(self, struct) -> None:
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

    def collect_from_enum(self, enum) -> None:
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

    def _collect_from_param(self, param) -> None:
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
                self.expression_scanner.scan_expression(stmt.value)

        elif isinstance(stmt, Foreach):
            # Foreach loop with type annotation
            if stmt.item_type is not None:
                self._collect_from_type(stmt.item_type)
            # NEW: Scan iterable expression
            if stmt.iterable is not None:
                self.expression_scanner.scan_expression(stmt.iterable)
            # Also check body
            self._collect_from_block(stmt.body)

        elif isinstance(stmt, If):
            # If statement - check all arms and else block
            for cond, block in stmt.arms:
                # NEW: Scan condition expression
                self.expression_scanner.scan_expression(cond)
                self._collect_from_block(block)
            if stmt.else_block is not None:
                self._collect_from_block(stmt.else_block)

        elif isinstance(stmt, While):
            # NEW: Scan condition expression
            if stmt.cond is not None:
                self.expression_scanner.scan_expression(stmt.cond)
            # While statement - check body
            self._collect_from_block(stmt.body)

        elif isinstance(stmt, Match):
            # NEW: Scan scrutinee expression
            if stmt.scrutinee is not None:
                self.expression_scanner.scan_expression(stmt.scrutinee)
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
                self.expression_scanner.scan_expression(stmt.value)

        elif isinstance(stmt, (ExprStmt, Print, PrintLn)):
            # NEW: Scan expression/value
            expr = stmt.expr if hasattr(stmt, 'expr') else stmt.value
            if expr is not None:
                self.expression_scanner.scan_expression(expr)

        elif isinstance(stmt, Rebind):
            # NEW: Scan rebind value
            if stmt.value is not None:
                self.expression_scanner.scan_expression(stmt.value)

        elif isinstance(stmt, (Break, Continue)):
            # These statements don't have expressions
            pass

    def _collect_from_type(self, ty: "Type") -> None:
        """Collect generic instantiations from a type annotation.

        This is the core method that detects GenericTypeRef instances and
        records them in the instantiations set.
        """
        from semantics.type_resolution import resolve_unknown_type, contains_unresolvable_unknown_type

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

    def _resolve_type_args(self, type_args: tuple["Type", ...]) -> tuple["Type", ...]:
        """Resolve all UnknownType instances in type_args to StructType or EnumType if possible."""
        from semantics.typesys import ArrayType, DynamicArrayType
        from semantics.type_resolution import resolve_unknown_type

        resolved_args = []
        for arg in type_args:
            resolved_arg = resolve_unknown_type(
                arg,
                self.expression_scanner.type_inferrer.struct_table or {},
                self.expression_scanner.type_inferrer.enum_table or {}
            )

            # Recursively resolve nested types
            if isinstance(resolved_arg, (ArrayType, DynamicArrayType)):
                resolved_base = resolve_unknown_type(
                    resolved_arg.base_type,
                    self.expression_scanner.type_inferrer.struct_table or {},
                    self.expression_scanner.type_inferrer.enum_table or {}
                )
                if isinstance(resolved_arg, ArrayType):
                    resolved_arg = ArrayType(base_type=resolved_base, size=resolved_arg.size)
                else:
                    resolved_arg = DynamicArrayType(base_type=resolved_base)
            elif isinstance(resolved_arg, GenericTypeRef):
                resolved_nested_args = self._resolve_type_args(resolved_arg.type_args)
                resolved_arg = GenericTypeRef(base_name=resolved_arg.base_name, type_args=resolved_nested_args)

            resolved_args.append(resolved_arg)

        return tuple(resolved_args)

    def _contains_unresolvable_unknown_type_in_tuple(self, type_args: tuple["Type", ...]) -> bool:
        """Check if any type argument tuple contains UnknownType that cannot be resolved.

        This is a wrapper around the centralized contains_unresolvable_unknown_type
        that handles tuple iteration.
        """
        from semantics.type_resolution import contains_unresolvable_unknown_type

        for arg in type_args:
            if contains_unresolvable_unknown_type(
                arg,
                self.expression_scanner.type_inferrer.struct_table or {},
                self.expression_scanner.type_inferrer.enum_table or {}
            ):
                return True
        return False
