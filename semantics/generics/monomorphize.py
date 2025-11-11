# semantics/generics/monomorphize.py
"""
Pass 1.6: Monomorphization

Generates concrete EnumType instances for each generic instantiation.
Takes GenericEnumType + type arguments → produces concrete EnumType.

Example: GenericEnumType("Result", ["T"]) + [i32] → EnumType("Result<i32>")

This pass performs type parameter substitution, replacing generic type
parameters (like T) with concrete types (like i32) in all variant
associated types.

Nested Generics Support:
The _substitute_type() method recursively handles nested generic types like
Result<Maybe<i32>> by:
1. Recursively substituting type arguments in GenericTypeRef nodes
2. Checking the cache for already-monomorphized nested generics
3. Recursively monomorphizing nested generics on-demand
This ensures zero-overhead monomorphization for arbitrarily nested generics.
"""
from __future__ import annotations
from typing import Dict, Tuple, Set
from dataclasses import dataclass, field

from semantics.generics.types import GenericEnumType, GenericStructType, TypeParameter
from semantics.generics.name_mangling import mangle_function_name
from semantics.typesys import Type, EnumType, EnumVariantInfo, StructType
from internals.report import Reporter
from internals import errors as er

# Import constraint validator for perk constraint checking
try:
    from semantics.generics.constraints import ConstraintValidator
except ImportError:
    ConstraintValidator = None  # Graceful degradation if not available


@dataclass
class Monomorphizer:
    """Generates concrete enum types and function signatures from generic definitions.

    For each generic type instantiation (e.g., Result<i32>), this class:
    1. Takes the GenericEnumType definition (Result<T>)
    2. Creates a substitution map (T → i32)
    3. Substitutes T with i32 in all variant associated types
    4. Produces a concrete EnumType with a unique name (Result<i32>)

    For each generic function instantiation (e.g., identity<i32>), this class:
    1. Takes the GenericFuncDef definition (identity<T>)
    2. Creates a substitution map (T → i32)
    3. Substitutes T with i32 in parameters and return type
    4. Produces a concrete FuncSig with a mangled name (identity__i32)

    The resulting EnumType and FuncSig instances can be used by the rest of the compiler
    as if they were regular (non-generic) types and functions.
    """

    reporter: Reporter
    # Constraint validator for checking perk constraints on type parameters
    constraint_validator: 'ConstraintValidator | None' = None
    # Cache of already-monomorphized enum types: (base_name, type_args) → EnumType
    # Prevents re-generating the same concrete type multiple times
    cache: Dict[Tuple[str, Tuple[Type, ...]], EnumType] = field(default_factory=dict)
    # Cache of already-monomorphized struct types: (base_name, type_args) → StructType
    struct_cache: Dict[Tuple[str, Tuple[Type, ...]], StructType] = field(default_factory=dict)
    # NEW: Cache of already-monomorphized function definitions: (base_name, type_args) → FuncDef
    func_cache: Dict[Tuple[str, Tuple[Type, ...]], 'FuncDef'] = field(default_factory=dict)
    # Generic enum table for recursive monomorphization
    generic_enums: Dict[str, GenericEnumType] = field(default_factory=dict)
    # Generic struct table for recursive monomorphization
    generic_structs: Dict[str, GenericStructType] = field(default_factory=dict)
    # NEW: Generic function table for function monomorphization
    generic_funcs: Dict[str, 'GenericFuncDef'] = field(default_factory=dict)
    # NEW: Function table for registering monomorphized functions
    func_table: 'FunctionTable | None' = None
    # NEW: Track monomorphized functions for type validation (maps mangled_name → (generic_name, type_args))
    monomorphized_functions: Dict[str, Tuple[str, Tuple[Type, ...]]] = field(default_factory=dict)
    # NEW: Enum table for registering on-demand monomorphized enums
    enum_table: 'EnumTable | None' = None
    # NEW: Struct table for registering on-demand monomorphized structs
    struct_table: 'StructTable | None' = None

    def _validate_type_constraints(
        self,
        type_params: Tuple,
        type_args: Tuple[Type, ...]
    ) -> None:
        """Validate perk constraints on type arguments (DRY helper).

        Args:
            type_params: Type parameters (may contain BoundedTypeParam with constraints)
            type_args: Concrete type arguments to validate

        Emits CE4006 errors for constraint violations.
        """
        if self.constraint_validator is None:
            return

        from semantics.ast import BoundedTypeParam
        for param, arg in zip(type_params, type_args):
            # All params are now BoundedTypeParam (may have empty constraints list)
            if isinstance(param, BoundedTypeParam) and param.constraints:
                # Validate all constraints on this type parameter
                self.constraint_validator.validate_all_constraints(param, arg, None)

    def monomorphize_all(
        self,
        generic_enums: Dict[str, GenericEnumType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, EnumType]:
        """Monomorphize all collected generic instantiations.

        Args:
            generic_enums: Table of GenericEnumType definitions (from CollectorPass)
            instantiations: Set of (base_name, type_args) tuples (from InstantiationCollector)

        Returns:
            Dictionary mapping concrete type names (e.g., "Result<i32>") to EnumType instances
        """
        # Store generic enums for recursive monomorphization
        self.generic_enums = generic_enums

        concrete_enums: Dict[str, EnumType] = {}

        for base_name, type_args in instantiations:
            # Look up the generic enum definition
            if base_name not in generic_enums:
                # This shouldn't happen if type validation passes, but be defensive
                continue

            generic = generic_enums[base_name]

            # Generate concrete enum type
            concrete = self.monomorphize_enum(generic, type_args)

            # Store by concrete name (e.g., "Result<i32>")
            concrete_enums[concrete.name] = concrete

        return concrete_enums

    def monomorphize_enum(
        self,
        generic: GenericEnumType,
        type_args: Tuple[Type, ...]
    ) -> EnumType:
        """Create concrete enum by substituting type parameters.

        Args:
            generic: The generic enum definition (e.g., Result<T>)
            type_args: Concrete type arguments (e.g., (BuiltinType.I32,))

        Returns:
            Concrete EnumType with substituted types
        """
        # Check cache first
        cache_key = (generic.name, type_args)
        if cache_key in self.cache:
            return self.cache[cache_key]

        # Validate that number of type arguments matches number of type parameters
        if len(type_args) != len(generic.type_params):
            # This shouldn't happen if type validation passes, but emit error to be safe
            er.emit(
                self.reporter,
                er.ERR.CE2001,  # Use generic type error for now
                None,
                name=f"{generic.name}<{', '.join(str(t) for t in type_args)}>"
            )
            # Return a dummy enum to continue compilation
            return EnumType(name=f"{generic.name}<error>", variants=())

        # Validate perk constraints on type arguments (Phase 4)
        self._validate_type_constraints(generic.type_params, type_args)

        # Build substitution map: type parameter name → concrete type
        # Example: {"T": BuiltinType.I32}
        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args):
            substitution[param.name] = arg

        # Substitute type parameters in all variants
        concrete_variants = []
        for variant in generic.variants:
            # Substitute in associated types
            concrete_associated_types = []
            for assoc_type in variant.associated_types:
                concrete_type = self._substitute_type(assoc_type, substitution)
                concrete_associated_types.append(concrete_type)

            concrete_variants.append(EnumVariantInfo(
                name=variant.name,
                associated_types=tuple(concrete_associated_types)
            ))

        # Generate unique name for concrete type
        # Example: "Result<i32>", "Result<string>", "Result<MyStruct>"
        concrete_name = self._generate_concrete_name(generic.name, type_args)

        # Create concrete EnumType with generic metadata
        concrete = EnumType(
            name=concrete_name,
            variants=tuple(concrete_variants),
            generic_base=generic.name,
            generic_args=type_args
        )

        # Cache the result
        self.cache[cache_key] = concrete

        return concrete

    def monomorphize_all_structs(
        self,
        generic_structs: Dict[str, GenericStructType],
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> Dict[str, StructType]:
        """Monomorphize all collected generic struct instantiations.

        Args:
            generic_structs: Table of GenericStructType definitions (from CollectorPass)
            instantiations: Set of (base_name, type_args) tuples (from InstantiationCollector)

        Returns:
            Dictionary mapping concrete type names (e.g., "Pair<i32, string>") to StructType instances
        """
        # Store generic structs for recursive monomorphization
        self.generic_structs = generic_structs

        concrete_structs: Dict[str, StructType] = {}

        for base_name, type_args in instantiations:
            # Look up the generic struct definition
            if base_name not in generic_structs:
                # This shouldn't happen if type validation passes, but be defensive
                continue

            generic = generic_structs[base_name]

            # Generate concrete struct type
            concrete = self.monomorphize_struct(generic, type_args)

            # Store by concrete name (e.g., "Pair<i32, string>")
            concrete_structs[concrete.name] = concrete

        return concrete_structs

    def monomorphize_struct(
        self,
        generic: GenericStructType,
        type_args: Tuple[Type, ...]
    ) -> StructType:
        """Create concrete struct by substituting type parameters.

        Args:
            generic: The generic struct definition (e.g., Pair<T, U>)
            type_args: Concrete type arguments (e.g., (BuiltinType.I32, BuiltinType.STRING))

        Returns:
            Concrete StructType with substituted types
        """
        # Check cache first
        cache_key = (generic.name, type_args)
        if cache_key in self.struct_cache:
            return self.struct_cache[cache_key]

        # Validate that number of type arguments matches number of type parameters
        if len(type_args) != len(generic.type_params):
            # This shouldn't happen if type validation passes, but emit error to be safe
            er.emit(
                self.reporter,
                er.ERR.CE2001,  # Use generic type error for now
                None,
                name=f"{generic.name}<{', '.join(str(t) for t in type_args)}>"
            )
            # Return a dummy struct to continue compilation
            return StructType(name=f"{generic.name}<error>", fields=())

        # Validate perk constraints on type arguments (Phase 4)
        self._validate_type_constraints(generic.type_params, type_args)

        # Build substitution map: type parameter name → concrete type
        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args):
            substitution[param.name] = arg

        # Substitute type parameters in all fields
        concrete_fields = []
        for field_name, field_type in generic.fields:
            concrete_type = self._substitute_type(field_type, substitution)
            concrete_fields.append((field_name, concrete_type))

        # Generate unique name for concrete type
        concrete_name = self._generate_concrete_name(generic.name, type_args)

        # Create concrete StructType with generic metadata
        concrete = StructType(
            name=concrete_name,
            fields=tuple(concrete_fields),
            generic_base=generic.name,
            generic_args=type_args
        )

        # Cache the result
        self.struct_cache[cache_key] = concrete

        return concrete

    def _substitute_type(self, ty: Type, substitution: Dict[str, Type]) -> Type:
        """Recursively substitute type parameters in a type.

        Args:
            ty: The type to substitute in (may contain TypeParameter or UnknownType instances representing type parameters)
            substitution: Map from type parameter names to concrete types

        Returns:
            Type with all TypeParameters replaced by concrete types
        """
        # If this is a type parameter, substitute it
        if isinstance(ty, TypeParameter):
            if ty.name in substitution:
                result = substitution[ty.name]
                # If the result is a GenericTypeRef, recursively resolve it to an EnumType
                from semantics.generics.types import GenericTypeRef
                if isinstance(result, GenericTypeRef):
                    return self._substitute_type(result, {})
                return result
            else:
                # Unknown type parameter - shouldn't happen, but be defensive
                return ty

        # Handle UnknownType that represents a type parameter (e.g., UnknownType("T"))
        # This happens when the AST builder creates UnknownType for type parameter references
        from semantics.typesys import UnknownType
        if isinstance(ty, UnknownType):
            if ty.name in substitution:
                # This UnknownType is actually a type parameter reference
                return substitution[ty.name]
            # Otherwise, it's a real unknown type (struct/enum) - pass through
            return ty

        # For pointer types, substitute the pointee type
        from semantics.typesys import PointerType
        if isinstance(ty, PointerType):
            return PointerType(
                pointee_type=self._substitute_type(ty.pointee_type, substitution)
            )

        # For array types, substitute the element type
        from semantics.typesys import ArrayType, DynamicArrayType
        if isinstance(ty, ArrayType):
            return ArrayType(
                base_type=self._substitute_type(ty.base_type, substitution),
                size=ty.size
            )
        elif isinstance(ty, DynamicArrayType):
            return DynamicArrayType(
                base_type=self._substitute_type(ty.base_type, substitution)
            )

        # For struct types, substitute field types
        from semantics.typesys import StructType
        if isinstance(ty, StructType):
            new_fields = []
            for field_name, field_type in ty.fields:
                new_field_type = self._substitute_type(field_type, substitution)
                new_fields.append((field_name, new_field_type))
            return StructType(name=ty.name, fields=tuple(new_fields))

        # For enum types, substitute variant associated types
        if isinstance(ty, EnumType):
            new_variants = []
            for variant in ty.variants:
                new_assoc_types = []
                for assoc_type in variant.associated_types:
                    new_assoc_type = self._substitute_type(assoc_type, substitution)
                    new_assoc_types.append(new_assoc_type)
                new_variants.append(EnumVariantInfo(
                    name=variant.name,
                    associated_types=tuple(new_assoc_types)
                ))
            return EnumType(name=ty.name, variants=tuple(new_variants))

        # For GenericTypeRef, recursively substitute type arguments and resolve to concrete EnumType or StructType
        from semantics.generics.types import GenericTypeRef
        if isinstance(ty, GenericTypeRef):
            # First, recursively substitute any type parameters in the type arguments
            new_type_args = []
            for arg in ty.type_args:
                new_arg = self._substitute_type(arg, substitution)
                new_type_args.append(new_arg)

            # Check if we've already monomorphized this generic enum
            # This handles nested generics like Result<Either<i32, string>>
            cache_key = (ty.base_name, tuple(new_type_args))
            if cache_key in self.cache:
                # Already monomorphized - return the concrete EnumType
                return self.cache[cache_key]

            # Check if we've already monomorphized this generic struct
            # This handles nested generics like Box<Box<i32>>
            if cache_key in self.struct_cache:
                # Already monomorphized - return the concrete StructType
                return self.struct_cache[cache_key]

            # If not in cache, recursively monomorphize it now
            # This ensures nested generics are fully resolved
            if ty.base_name in self.generic_enums:
                generic = self.generic_enums[ty.base_name]
                concrete = self.monomorphize_enum(generic, tuple(new_type_args))
                return concrete

            # Check if it's a generic struct
            if ty.base_name in self.generic_structs:
                generic = self.generic_structs[ty.base_name]
                concrete = self.monomorphize_struct(generic, tuple(new_type_args))
                return concrete

            # If it's not a known generic enum or struct, keep as GenericTypeRef
            # (this shouldn't happen for well-formed programs)
            return GenericTypeRef(
                base_name=ty.base_name,
                type_args=tuple(new_type_args)
            )

        # For all other types (BuiltinType, etc.), return as-is
        return ty

    def _generate_concrete_name(self, base_name: str, type_args: Tuple[Type, ...]) -> str:
        """Generate a unique name for a concrete generic type.

        Args:
            base_name: Base generic name (e.g., "Result")
            type_args: Concrete type arguments (e.g., (BuiltinType.I32,))

        Returns:
            Concrete type name (e.g., "Result<i32>")
        """
        if not type_args:
            return base_name

        # Format type arguments as strings
        arg_strs = [self._type_to_string(arg) for arg in type_args]

        # Build concrete name: Result<i32>, Result<string>, etc.
        return f"{base_name}<{', '.join(arg_strs)}>"

    def _type_to_string(self, ty: Type) -> str:
        """Convert a type to its string representation for name generation.

        Args:
            ty: The type to convert

        Returns:
            String representation of the type
        """
        # Use the built-in str() which should work for all Type instances
        # BuiltinType, ArrayType, etc. all have __str__ implementations
        return str(ty)

    def monomorphize_function(
        self,
        generic: 'GenericFuncDef',
        type_args: Tuple[Type, ...]
    ) -> 'FuncDef':
        """Create concrete function from generic definition.

        Performs type parameter substitution in signature and body.
        Returns a complete FuncDef ready for validation and codegen.

        Args:
            generic: Generic function definition
            type_args: Concrete type arguments

        Returns:
            Concrete function definition (FuncDef) with substituted body
        """
        from semantics.passes.collect import Param
        from semantics.ast import FuncDef
        import copy

        # Check cache
        cache_key = (generic.name, type_args)
        if cache_key in self.func_cache:
            # Cache stores FuncDef now
            return self.func_cache[cache_key]

        # Validate type arguments count
        if len(type_args) != len(generic.type_params):
            # Should not happen if instantiation detection is correct
            raise ValueError(
                f"Type argument count mismatch: {generic.name} expects "
                f"{len(generic.type_params)} args, got {len(type_args)}"
            )

        # Validate perk constraints on type arguments
        self._validate_type_constraints(generic.type_params, type_args)

        # Build substitution map
        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args):
            param_name = param.name if hasattr(param, 'name') else str(param)
            substitution[param_name] = arg

        # Substitute in parameter types
        concrete_params = []
        for i, param in enumerate(generic.params):
            concrete_type = self._substitute_type(param.ty, substitution) if param.ty else None
            # Import AST Param which has 'loc', not collect.Param which has 'index'
            from semantics.ast import Param as AstParam
            concrete_params.append(AstParam(
                name=param.name,
                ty=concrete_type,
                name_span=param.name_span,
                type_span=param.type_span,
                loc=getattr(param, 'loc', None)
            ))

        # Substitute in return type
        concrete_ret = self._substitute_type(generic.ret, substitution) if generic.ret else None

        # Generate mangled name
        mangled_name = mangle_function_name(generic.name, type_args)

        # Track monomorphization for type validation
        self.monomorphized_functions[mangled_name] = (generic.name, type_args)

        # Scan function body for nested generic function calls BEFORE substitution
        # This allows us to recursively monomorphize any generic functions called within this one
        self._collect_nested_instantiations(generic.body, substitution, generic)

        # Deep copy the function body and substitute type parameters
        concrete_body = self._substitute_body(generic.body, substitution)

        # Create concrete function definition - copy from generic and update fields
        # This preserves loc and other fields we might not know about
        concrete_func = copy.copy(generic)
        concrete_func.name = mangled_name
        concrete_func.params = concrete_params
        concrete_func.ret = concrete_ret
        concrete_func.body = concrete_body
        concrete_func.type_params = None  # No longer generic

        # Cache result
        self.func_cache[cache_key] = concrete_func

        return concrete_func

    def _collect_nested_instantiations(self, body: 'Block', substitution: Dict[str, Type], generic_func: 'GenericFuncDef') -> None:
        """Scan function body for calls to other generic functions and recursively monomorphize them.

        This enables multi-pass monomorphization: when monomorphizing function A<T>, if it calls
        function B<T>, we detect that and recursively monomorphize B<concrete_type>.

        Args:
            body: Function body to scan
            substitution: Current type parameter substitution map (e.g., {"T": StructType("Point")})
            generic_func: The generic function being monomorphized (for parameter type info)
        """
        from semantics.ast import Let, Call, Name, ExprStmt, Return, If, While, Match, Foreach, Block

        # Build variable type map from function parameters
        var_types = {}
        for param in generic_func.params:
            if param.ty:
                # Substitute type parameters in parameter type
                concrete_ty = self._substitute_type(param.ty, substitution)
                var_types[param.name] = concrete_ty

        for stmt in body.statements:
            if isinstance(stmt, Let) and stmt.value:
                self._collect_from_expr(stmt.value, substitution, var_types)
            elif isinstance(stmt, ExprStmt):
                self._collect_from_expr(stmt.expr, substitution, var_types)
            elif isinstance(stmt, Return) and stmt.value:
                self._collect_from_expr(stmt.value, substitution, var_types)
            elif isinstance(stmt, If):
                for cond, block in stmt.arms:
                    self._collect_from_expr(cond, substitution, var_types)
                    self._collect_nested_instantiations(block, substitution, generic_func)
                if stmt.else_block:
                    self._collect_nested_instantiations(stmt.else_block, substitution, generic_func)
            elif isinstance(stmt, While):
                if stmt.cond:
                    self._collect_from_expr(stmt.cond, substitution, var_types)
                self._collect_nested_instantiations(stmt.body, substitution, generic_func)
            elif isinstance(stmt, Foreach):
                if stmt.iterable:
                    self._collect_from_expr(stmt.iterable, substitution, var_types)
                self._collect_nested_instantiations(stmt.body, substitution, generic_func)
            elif isinstance(stmt, Match):
                if stmt.scrutinee:
                    self._collect_from_expr(stmt.scrutinee, substitution, var_types)
                for arm in stmt.arms:
                    if isinstance(arm.body, Block):
                        self._collect_nested_instantiations(arm.body, substitution, generic_func)

    def _collect_from_expr(self, expr, substitution: Dict[str, Type], var_types: Dict[str, Type]) -> None:
        """Recursively scan expression for generic function calls.

        Args:
            expr: Expression to scan
            substitution: Type parameter substitution map
            var_types: Map from variable names to their concrete types
        """
        from semantics.ast import Call, Name, BinaryOp, UnaryOp, TryExpr, DotCall

        if isinstance(expr, Call) and isinstance(expr.callee, Name):
            function_name = expr.callee.id

            # Check if this is a generic function
            if self.generic_funcs and function_name in self.generic_funcs:
                generic_func = self.generic_funcs[function_name]

                # Infer type arguments by applying substitution to argument types
                type_args = self._infer_type_args_with_substitution(expr, generic_func, var_types)

                if type_args:
                    # Track this instantiation for later processing
                    # We don't monomorphize recursively here to avoid registration issues
                    # Instead, we add to a worklist that will be processed by monomorphize_all_functions
                    cache_key = (function_name, type_args)
                    if cache_key not in self.func_cache and hasattr(self, 'pending_instantiations'):
                        self.pending_instantiations.add(cache_key)

        elif isinstance(expr, BinaryOp):
            self._collect_from_expr(expr.left, substitution, var_types)
            self._collect_from_expr(expr.right, substitution, var_types)
        elif isinstance(expr, UnaryOp):
            self._collect_from_expr(expr.expr, substitution, var_types)
        elif isinstance(expr, TryExpr):
            self._collect_from_expr(expr.expr, substitution, var_types)
        elif isinstance(expr, DotCall):
            self._collect_from_expr(expr.receiver, substitution, var_types)
            for arg in expr.args:
                self._collect_from_expr(arg, substitution, var_types)

    def _infer_type_args_with_substitution(
        self,
        call: 'Call',
        generic_func: 'GenericFuncDef',
        var_types: Dict[str, Type]
    ) -> Optional[Tuple[Type, ...]]:
        """Infer type arguments for a generic function call inside another generic function.

        Args:
            call: Call AST node
            generic_func: Generic function being called
            var_types: Map from variable names to their concrete types

        Returns:
            Tuple of concrete types or None if inference fails
        """
        from semantics.ast import Name
        from semantics.typesys import UnknownType

        type_param_map = {}

        # Match each call argument to function parameter
        call_args = getattr(call, "args", []) or []
        if len(call_args) != len(generic_func.params):
            return None

        for arg_expr, param in zip(call_args, generic_func.params):
            # Infer argument type
            arg_type = None
            if isinstance(arg_expr, Name):
                arg_name = arg_expr.id
                # Look up in variable types map
                if arg_name in var_types:
                    arg_type = var_types[arg_name]

            if arg_type is None:
                # Can't infer, skip
                return None

            # Unify with parameter type
            if param.ty is None:
                return None

            # Simple unification: if param type is UnknownType (type param), assign it
            if isinstance(param.ty, UnknownType):
                param_name = str(param.ty)
                if param_name in type_param_map:
                    if type_param_map[param_name] != arg_type:
                        return None  # Conflict
                else:
                    type_param_map[param_name] = arg_type
            elif param.ty != arg_type:
                return None  # Type mismatch

        # Extract type args in order
        type_args = []
        for tp in generic_func.type_params:
            tp_name = tp.name if hasattr(tp, 'name') else str(tp)
            if tp_name not in type_param_map:
                return None  # Missing type arg
            type_args.append(type_param_map[tp_name])

        return tuple(type_args)

    def _substitute_body(self, body: 'Block', substitution: Dict[str, Type]) -> 'Block':
        """Deep copy a function body and substitute type parameters.

        Args:
            body: Original function body
            substitution: Map from type parameter names to concrete types

        Returns:
            New Block with substituted types
        """
        import copy

        # Deep copy all statements
        new_statements = []
        for stmt in body.statements:
            new_stmt = self._substitute_statement(stmt, substitution)
            new_statements.append(new_stmt)

        # Copy the Block and update statements
        result = copy.copy(body)
        result.statements = new_statements
        return result

    def _substitute_statement(self, stmt, substitution: Dict[str, Type]):
        """Recursively substitute types in a statement.

        Args:
            stmt: Statement AST node
            substitution: Type substitution map

        Returns:
            New statement with substituted types
        """
        import copy
        from semantics.ast import (
            Let, Rebind, If, While, Foreach, Return, Match, MatchArm,
            ExprStmt, Block, Break, Continue, Print, PrintLn
        )

        # Let statement - substitute type annotation
        if isinstance(stmt, Let):
            result = copy.copy(stmt)
            if stmt.ty:
                result.ty = self._substitute_type(stmt.ty, substitution)
            if stmt.value:
                result.value = self._substitute_expr(stmt.value, substitution)
            return result

        # Rebind statement (assignment)
        if isinstance(stmt, Rebind):
            result = copy.copy(stmt)
            result.target = self._substitute_expr(stmt.target, substitution)
            result.value = self._substitute_expr(stmt.value, substitution)
            return result

        # If statement
        if isinstance(stmt, If):
            result = copy.copy(stmt)
            # If has arms (list of condition-block pairs), substitute each condition
            result.arms = [
                (self._substitute_expr(cond, substitution), self._substitute_body(block, substitution))
                for cond, block in stmt.arms
            ]
            if stmt.else_block:
                result.else_block = self._substitute_body(stmt.else_block, substitution)
            return result

        # While statement
        if isinstance(stmt, While):
            result = copy.copy(stmt)
            result.cond = self._substitute_expr(stmt.cond, substitution)
            result.body = self._substitute_body(stmt.body, substitution)
            return result

        # Foreach statement
        if isinstance(stmt, Foreach):
            result = copy.copy(stmt)
            result.iterable = self._substitute_expr(stmt.iterable, substitution)
            result.body = self._substitute_body(stmt.body, substitution)
            return result

        # Print/PrintLn statements
        if isinstance(stmt, (Print, PrintLn)):
            result = copy.copy(stmt)
            result.value = self._substitute_expr(stmt.value, substitution)
            return result

        # Return statement
        if isinstance(stmt, Return):
            result = copy.copy(stmt)
            if stmt.value:
                result.value = self._substitute_expr(stmt.value, substitution)
            return result

        # Match statement
        if isinstance(stmt, Match):
            result = copy.copy(stmt)
            if stmt.scrutinee:
                result.scrutinee = self._substitute_expr(stmt.scrutinee, substitution)
            new_arms = []
            for arm in stmt.arms:
                new_arm = copy.copy(arm)
                # Substitute body (can be Expr or Block)
                if isinstance(arm.body, Block):
                    new_arm.body = self._substitute_body(arm.body, substitution)
                else:
                    new_arm.body = self._substitute_expr(arm.body, substitution)
                new_arms.append(new_arm)
            result.arms = new_arms
            return result

        # Expression statement
        if isinstance(stmt, ExprStmt):
            result = copy.copy(stmt)
            result.expr = self._substitute_expr(stmt.expr, substitution)
            return result

        # Break/Continue - no substitution needed
        if isinstance(stmt, (Break, Continue)):
            return copy.copy(stmt)

        # Unknown statement type - just copy
        return copy.deepcopy(stmt)

    def _substitute_expr(self, expr, substitution: Dict[str, Type]):
        """Recursively substitute types in an expression.

        Most expressions don't contain type annotations, so this is mostly
        a deep copy operation. The main exception is type casts (CastExpr).

        Args:
            expr: Expression AST node
            substitution: Type substitution map

        Returns:
            New expression (usually a deep copy)
        """
        import copy
        from semantics.ast import CastExpr, TryExpr

        # Cast expression - IMPORTANT: substitute target type
        if isinstance(expr, CastExpr):
            new_expr = self._substitute_expr(expr.expr, substitution)
            new_target_type = self._substitute_type(expr.target_type, substitution)
            result = copy.copy(expr)
            result.expr = new_expr
            result.target_type = new_target_type
            return result

        # Try expression (error propagation x??)
        if isinstance(expr, TryExpr):
            new_expr = self._substitute_expr(expr.expr, substitution)
            result = copy.copy(expr)
            result.expr = new_expr
            return result

        # For all other expressions, deep copy is sufficient
        # Expressions like Name, BinaryOp, Call, etc. don't contain type annotations
        return copy.deepcopy(expr)

    def _extract_type_instantiations(self, ty: Type, instantiations: Set[Tuple[str, Tuple[Type, ...]]]) -> None:
        """Recursively extract all generic type instantiations from a Type.

        This helper scans a type tree (e.g., Result<Maybe<i32>>) and collects all
        generic enum/struct instantiations that need to be monomorphized.

        Args:
            ty: Type to scan for generic instantiations
            instantiations: Set to add (base_name, type_args) tuples to
        """
        if ty is None:
            return

        # Check if this is a monomorphized enum type with generic metadata
        if isinstance(ty, EnumType) and hasattr(ty, 'generic_base') and ty.generic_base:
            # This is a concrete instantiation of a generic enum (e.g., Result<u64>)
            base_name = ty.generic_base
            type_args = ty.generic_args if hasattr(ty, 'generic_args') and ty.generic_args else tuple()
            if type_args:  # Only add if we have type arguments
                instantiations.add((base_name, type_args))
                # Recursively extract from type arguments
                for arg in type_args:
                    self._extract_type_instantiations(arg, instantiations)

        # Check if this is a monomorphized struct type with generic metadata
        elif isinstance(ty, StructType) and hasattr(ty, 'generic_base') and ty.generic_base:
            # This is a concrete instantiation of a generic struct (e.g., Pair<i32, string>)
            base_name = ty.generic_base
            type_args = ty.generic_args if hasattr(ty, 'generic_args') and ty.generic_args else tuple()
            if type_args:  # Only add if we have type arguments
                instantiations.add((base_name, type_args))
                # Recursively extract from type arguments
                for arg in type_args:
                    self._extract_type_instantiations(arg, instantiations)

        # Handle array types (which may contain generic elements)
        elif hasattr(ty, 'element_type'):
            self._extract_type_instantiations(ty.element_type, instantiations)

        # Handle reference types (which may contain generic targets)
        elif hasattr(ty, 'target_type'):
            self._extract_type_instantiations(ty.target_type, instantiations)

    def monomorphize_all_functions(
        self,
        function_instantiations: Set[Tuple[str, Tuple[Type, ...]]],
        program_or_units
    ) -> None:
        """Monomorphize all detected function instantiations.

        Generates concrete function definitions and adds them to FunctionTable and Program/Units.

        Args:
            function_instantiations: Set of (function_name, type_args) tuples
            program_or_units: Either a Program AST (single-file) or list of Units (multi-file)
        """
        from semantics.passes.collect import FuncSig
        from semantics.ast import Program

        if not self.func_table:
            # No function table to register in - should not happen
            return

        # Determine if single-file or multi-file mode
        is_single_file = isinstance(program_or_units, Program)
        target_program = program_or_units if is_single_file else None
        units = None if is_single_file else program_or_units

        # Use worklist approach to handle nested generic function calls
        # Initialize worklist with explicitly requested instantiations
        worklist = set(function_instantiations)
        processed = set()

        # Initialize pending instantiations set for nested call detection
        self.pending_instantiations = set()

        while worklist:
            func_name, type_args = worklist.pop()

            # Skip if already processed
            if (func_name, type_args) in processed:
                continue
            processed.add((func_name, type_args))
            # Look up generic function
            if func_name not in self.generic_funcs:
                # Should not happen if instantiation detection is correct
                continue

            generic_func = self.generic_funcs[func_name]

            # Monomorphize - returns FuncDef now
            concrete_func = self.monomorphize_function(generic_func, type_args)

            # Extract enum/struct instantiations from the function signature
            # This ensures that Result<T>, Maybe<T>, and other generic return/param types
            # are properly monomorphized even if they weren't detected by InstantiationCollector
            signature_instantiations = set()

            # IMPORTANT: In Sushi Lang, all functions implicitly return Result<T>
            # So if the function has a return type, we need to ensure Result<ret_type> exists
            if concrete_func.ret:
                # Add Result<ret_type> instantiation
                result_type_args = (concrete_func.ret,)
                signature_instantiations.add(("Result", result_type_args))

            # Check return type for nested generics
            self._extract_type_instantiations(concrete_func.ret, signature_instantiations)
            # Check parameter types
            for param in concrete_func.params:
                self._extract_type_instantiations(param.ty, signature_instantiations)

            # Monomorphize any new enum/struct types discovered in the signature
            for base_name, sig_type_args in signature_instantiations:
                if base_name in self.generic_enums:
                    # Monomorphize enum (e.g., Result<u64>)
                    concrete_enum = self.monomorphize_enum(self.generic_enums[base_name], sig_type_args)
                    # Register in global enum table if available
                    if self.enum_table and concrete_enum.name not in self.enum_table.by_name:
                        self.enum_table.by_name[concrete_enum.name] = concrete_enum
                        self.enum_table.order.append(concrete_enum.name)
                elif base_name in self.generic_structs:
                    # Monomorphize struct (e.g., Pair<i32, string>)
                    concrete_struct = self.monomorphize_struct(self.generic_structs[base_name], sig_type_args)
                    # Register in global struct table if available
                    if self.struct_table and concrete_struct.name not in self.struct_table.by_name:
                        self.struct_table.by_name[concrete_struct.name] = concrete_struct
                        self.struct_table.order.append(concrete_struct.name)

            # Get mangled name
            mangled_name = concrete_func.name

            # Check for conflicts (shouldn't happen with mangling, but be safe)
            if mangled_name in self.func_table.by_name:
                # Already monomorphized (from cache)
                continue

            # Create FuncSig for function table
            concrete_sig = FuncSig(
                name=mangled_name,
                params=concrete_func.params,
                ret_type=concrete_func.ret,
                ret_span=concrete_func.ret_span,
                is_public=concrete_func.is_public,
                loc=None,
                name_span=concrete_func.name_span,
                unit_name=None
            )

            # Register in function table
            self.func_table.by_name[mangled_name] = concrete_sig
            self.func_table.order.append(mangled_name)

            # Add to program/unit functions list for backend emission
            if is_single_file:
                target_program.functions.append(concrete_func)
            else:
                # Multi-file mode: add to the main unit (first unit with main function)
                # For now, just add to the first unit's AST
                if units and len(units) > 0 and units[0].ast:
                    units[0].ast.functions.append(concrete_func)

            # After monomorphizing, check if any new instantiations were discovered
            # and add them to the worklist
            worklist.update(self.pending_instantiations)
            self.pending_instantiations.clear()
