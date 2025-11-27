# semantics/generics/monomorphize/functions.py
"""
Generic function monomorphization.

This module handles the monomorphization of generic functions, including:
- Converting generic function definitions into concrete instantiations
- Detecting nested generic function calls
- Type argument inference from call sites
- Managing worklists for multi-pass monomorphization
"""
from __future__ import annotations
from typing import Dict, Tuple, Set, Optional, TYPE_CHECKING
import copy

from semantics.generics.name_mangling import mangle_function_name
from semantics.typesys import Type

if TYPE_CHECKING:
    from semantics.ast import Block, Call


class FunctionMonomorphizer:
    """Handles monomorphization of generic functions.

    This class is responsible for:
    - Converting GenericFuncDef + type args → concrete FuncDef
    - Detecting and recursively monomorphizing nested generic function calls
    - Inferring type arguments from call sites
    - Managing function caches and registration
    """

    def __init__(self, monomorphizer):
        """Initialize function monomorphizer.

        Args:
            monomorphizer: Parent Monomorphizer instance (provides access to
                           caches, generic tables, and substitutor)
        """
        self.monomorphizer = monomorphizer

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
        from semantics.ast import FuncDef, Param as AstParam

        # Check cache
        cache_key = (generic.name, type_args)
        if cache_key in self.monomorphizer.func_cache:
            # Cache stores FuncDef now
            return self.monomorphizer.func_cache[cache_key]

        # Validate type arguments count
        if len(type_args) != len(generic.type_params):
            # Should not happen if instantiation detection is correct
            raise ValueError(
                f"Type argument count mismatch: {generic.name} expects "
                f"{len(generic.type_params)} args, got {len(type_args)}"
            )

        # Validate perk constraints on type arguments
        self.monomorphizer._validate_type_constraints(generic.type_params, type_args)

        # Build substitution map
        substitution: Dict[str, Type] = {}
        for param, arg in zip(generic.type_params, type_args):
            param_name = param.name if hasattr(param, 'name') else str(param)
            substitution[param_name] = arg

        # Substitute in parameter types
        concrete_params = []
        for param in generic.params:
            concrete_type = self.monomorphizer.substitutor.substitute_type(
                param.ty, substitution
            ) if param.ty else None
            concrete_params.append(AstParam(
                name=param.name,
                ty=concrete_type,
                name_span=param.name_span,
                type_span=param.type_span,
                loc=getattr(param, 'loc', None)
            ))

        # Substitute in return type
        concrete_ret = self.monomorphizer.substitutor.substitute_type(
            generic.ret, substitution
        ) if generic.ret else None

        # Generate mangled name
        mangled_name = mangle_function_name(generic.name, type_args)

        # Track monomorphization for type validation
        self.monomorphizer.monomorphized_functions[mangled_name] = (generic.name, type_args)

        # Scan function body for nested generic function calls BEFORE substitution
        # This allows us to recursively monomorphize any generic functions called within this one
        self._collect_nested_instantiations(generic.body, substitution, generic)

        # Deep copy the function body and substitute type parameters
        concrete_body = self.monomorphizer.substitutor.substitute_body(generic.body, substitution)

        # Create concrete function definition - copy from generic and update fields
        # This preserves loc and other fields we might not know about
        concrete_func = copy.copy(generic)
        concrete_func.name = mangled_name
        concrete_func.params = concrete_params
        concrete_func.ret = concrete_ret
        concrete_func.body = concrete_body
        concrete_func.type_params = None  # No longer generic

        # Cache result
        self.monomorphizer.func_cache[cache_key] = concrete_func

        return concrete_func

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

        if not self.monomorphizer.func_table:
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
        self.monomorphizer.pending_instantiations = set()

        while worklist:
            func_name, type_args = worklist.pop()

            # Skip if already processed
            if (func_name, type_args) in processed:
                continue
            processed.add((func_name, type_args))

            # Look up generic function
            if func_name not in self.monomorphizer.generic_funcs:
                # Should not happen if instantiation detection is correct
                continue

            generic_func = self.monomorphizer.generic_funcs[func_name]

            # Monomorphize - returns FuncDef now
            concrete_func = self.monomorphize_function(generic_func, type_args)

            # Extract enum/struct instantiations from the function signature
            # This ensures that Result<T>, Maybe<T>, and other generic return/param types
            # are properly monomorphized even if they weren't detected by InstantiationCollector
            signature_instantiations = set()

            # IMPORTANT: In Sushi Lang, all functions implicitly return Result<T, E>
            # So if the function has a return type, we need to ensure Result<ret_type, err_type> exists
            if concrete_func.ret:
                # Get error type from function signature or default to StdError
                if hasattr(concrete_func, 'err_type') and concrete_func.err_type:
                    err_type = concrete_func.err_type
                else:
                    # Default to StdError
                    err_type = self.monomorphizer.enum_table.by_name.get("StdError") if self.monomorphizer.enum_table else None

                if err_type:
                    # Add Result<ret_type, err_type> instantiation
                    result_type_args = (concrete_func.ret, err_type)
                    signature_instantiations.add(("Result", result_type_args))

            # Check return type for nested generics
            self._extract_type_instantiations(concrete_func.ret, signature_instantiations)
            # Check parameter types
            for param in concrete_func.params:
                self._extract_type_instantiations(param.ty, signature_instantiations)

            # Monomorphize any new enum/struct types discovered in the signature
            for base_name, sig_type_args in signature_instantiations:
                if base_name in self.monomorphizer.generic_enums:
                    # Monomorphize enum (e.g., Result<u64>)
                    concrete_enum = self.monomorphizer.monomorphize_enum(
                        self.monomorphizer.generic_enums[base_name], sig_type_args
                    )
                    # Register in global enum table if available
                    if self.monomorphizer.enum_table and concrete_enum.name not in self.monomorphizer.enum_table.by_name:
                        self.monomorphizer.enum_table.by_name[concrete_enum.name] = concrete_enum
                        self.monomorphizer.enum_table.order.append(concrete_enum.name)
                elif base_name in self.monomorphizer.generic_structs:
                    # Monomorphize struct (e.g., Pair<i32, string>)
                    concrete_struct = self.monomorphizer.monomorphize_struct(
                        self.monomorphizer.generic_structs[base_name], sig_type_args
                    )
                    # Register in global struct table if available
                    if self.monomorphizer.struct_table and concrete_struct.name not in self.monomorphizer.struct_table.by_name:
                        self.monomorphizer.struct_table.by_name[concrete_struct.name] = concrete_struct
                        self.monomorphizer.struct_table.order.append(concrete_struct.name)

            # Get mangled name
            mangled_name = concrete_func.name

            # Check for conflicts (shouldn't happen with mangling, but be safe)
            if mangled_name in self.monomorphizer.func_table.by_name:
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
            self.monomorphizer.func_table.by_name[mangled_name] = concrete_sig
            self.monomorphizer.func_table.order.append(mangled_name)

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
            worklist.update(self.monomorphizer.pending_instantiations)
            self.monomorphizer.pending_instantiations.clear()

    def _collect_nested_instantiations(
        self,
        body: 'Block',
        substitution: Dict[str, Type],
        generic_func: 'GenericFuncDef'
    ) -> None:
        """Scan function body for calls to other generic functions and recursively monomorphize them.

        This enables multi-pass monomorphization: when monomorphizing function A<T>, if it calls
        function B<T>, we detect that and recursively monomorphize B<concrete_type>.

        Args:
            body: Function body to scan
            substitution: Current type parameter substitution map (e.g., {"T": StructType("Point")})
            generic_func: The generic function being monomorphized (for parameter type info)
        """
        from semantics.ast import Let, ExprStmt, Return, If, While, Match, Foreach, Block

        # Build variable type map from function parameters
        var_types = {}
        for param in generic_func.params:
            if param.ty:
                # Substitute type parameters in parameter type
                concrete_ty = self.monomorphizer.substitutor.substitute_type(param.ty, substitution)
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
            if self.monomorphizer.generic_funcs and function_name in self.monomorphizer.generic_funcs:
                generic_func = self.monomorphizer.generic_funcs[function_name]

                # Infer type arguments by applying substitution to argument types
                type_args = self._infer_type_args_with_substitution(expr, generic_func, var_types)

                if type_args:
                    # Track this instantiation for later processing
                    # We don't monomorphize recursively here to avoid registration issues
                    # Instead, we add to a worklist that will be processed by monomorphize_all_functions
                    cache_key = (function_name, type_args)
                    if cache_key not in self.monomorphizer.func_cache and hasattr(self.monomorphizer, 'pending_instantiations'):
                        self.monomorphizer.pending_instantiations.add(cache_key)

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

    def _extract_type_instantiations(
        self,
        ty: Type,
        instantiations: Set[Tuple[str, Tuple[Type, ...]]]
    ) -> None:
        """Recursively extract all generic type instantiations from a Type.

        This helper scans a type tree (e.g., Result<Maybe<i32>>) and collects all
        generic enum/struct instantiations that need to be monomorphized.

        Args:
            ty: Type to scan for generic instantiations
            instantiations: Set to add (base_name, type_args) tuples to
        """
        from semantics.typesys import EnumType, StructType

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
