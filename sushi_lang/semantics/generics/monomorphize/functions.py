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

from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from sushi_lang.semantics.generics.types import TypePack
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Block, Call


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

    def build_substitution(
        self,
        generic: 'GenericFuncDef',
        type_args: Tuple[Type, ...]
    ) -> Dict[str, "Type | TypePack"]:
        """Build the type-parameter -> binding substitution map for a generic.

        Handles two cases:

        - **No pack param** (the common, pre-existing case): requires an exact
          1:1 arity match between ``generic.type_params`` and ``type_args``;
          otherwise raises ``ValueError`` (same message as before). Each param
          binds to a single ``Type``. Perk constraints are validated as before.
        - **Trailing pack param** (``getattr(tp, 'is_pack', False)`` truthy):
          the pack param must be the *last* type-param and there may be at most
          one. The leading ``k = len(tps) - 1`` non-pack params bind 1:1 to
          ``type_args[:k]``; the pack param binds to a ``TypePack`` absorbing
          all trailing args ``type_args[k:]`` (zero or more). Requires
          ``len(type_args) >= k``.

        Args:
            generic: Generic function definition (its ``type_params`` list).
            type_args: Flat, ordered tuple of concrete type arguments.

        Returns:
            Mapping from type-parameter name to ``Type`` (regular binding) or
            ``TypePack`` (pack binding).
        """
        tps = list(generic.type_params)

        # Locate pack params (at most one, must be last).
        pack_indices = [i for i, tp in enumerate(tps) if getattr(tp, 'is_pack', False)]

        if not pack_indices:
            # --- No pack param: byte-for-byte unchanged behavior ---
            if len(type_args) != len(generic.type_params):
                # Should not happen if instantiation detection is correct
                raise ValueError(
                    f"Type argument count mismatch: {generic.name} expects "
                    f"{len(generic.type_params)} args, got {len(type_args)}"
                )

            # Validate perk constraints on type arguments
            self.monomorphizer._validate_type_constraints(generic.type_params, type_args)

            substitution: Dict[str, "Type | TypePack"] = {}
            for param, arg in zip(generic.type_params, type_args):
                param_name = param.name if hasattr(param, 'name') else str(param)
                substitution[param_name] = arg
            return substitution

        # --- Pack param present ---
        if len(pack_indices) > 1:
            raise ValueError(
                f"{generic.name} declares {len(pack_indices)} pack type-parameters; "
                f"at most one is allowed"
            )

        k = pack_indices[0]
        if k != len(tps) - 1:
            raise ValueError(
                f"{generic.name} declares a pack type-parameter that is not the "
                f"last type-parameter (at index {k} of {len(tps)})"
            )

        if len(type_args) < k:
            raise ValueError(
                f"Type argument count mismatch: {generic.name} expects at least "
                f"{k} args, got {len(type_args)}"
            )

        leading_params = tps[:k]
        leading_args = type_args[:k]

        # Validate perk constraints only on the leading 1:1 params.
        self.monomorphizer._validate_type_constraints(leading_params, leading_args)

        substitution = {}
        for param, arg in zip(leading_params, leading_args):
            param_name = param.name if hasattr(param, 'name') else str(param)
            substitution[param_name] = arg

        pack_param = tps[k]
        pack_name = pack_param.name if hasattr(pack_param, 'name') else str(pack_param)
        substitution[pack_name] = TypePack(tuple(type_args[k:]))
        return substitution

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
        # Check cache
        cache_key = (generic.name, type_args)
        if cache_key in self.monomorphizer.func_cache:
            # Cache stores FuncDef now
            return self.monomorphizer.func_cache[cache_key]

        # Build substitution map (arity-checked; supports an optional trailing
        # pack type-parameter that absorbs all trailing type args).
        substitution = self.build_substitution(generic, type_args)

        # Substitute in parameter types. A pack-typed value-parameter fans out
        # into N concrete params (one per pack element, possibly zero); a normal
        # param yields exactly one concrete param identical to the legacy result.
        concrete_params = []
        for param in generic.params:
            concrete_params.extend(
                self.monomorphizer.substitutor.expand_pack_param(param, substitution)
            )

        # Substitute in return type
        concrete_ret = self.monomorphizer.substitutor.substitute_type(
            generic.ret, substitution
        ) if generic.ret else None

        # Generate mangled name. If the generic has a trailing pack type-param
        # (T1 convention: at most one, always last), pass its arity so the
        # symbol is distinct per pack size and never collides with a regular
        # generic of the same base. Non-pack generics call exactly as before.
        type_params = generic.type_params or []
        has_pack = bool(type_params) and getattr(type_params[-1], 'is_pack', False)
        if has_pack:
            pack_arity = len(type_args) - (len(type_params) - 1)
            mangled_name = mangle_function_name(
                generic.name, type_args, pack_arity=pack_arity
            )
        else:
            mangled_name = mangle_function_name(generic.name, type_args)

        # Track monomorphization for type validation
        self.monomorphizer.monomorphized_functions[mangled_name] = (generic.name, type_args)

        # Scan function body for nested generic function calls BEFORE substitution
        # This allows us to recursively monomorphize any generic functions called within this one
        self._collect_nested_instantiations(generic.body, substitution, generic)

        # Deep copy the function body and substitute type parameters
        concrete_body = self.monomorphizer.substitutor.substitute_body(generic.body, substitution)

        # Build the pack value-parameter fan-out map and unroll any expand(...)
        # statements in the now-concrete body into ordinary statements. After
        # this, no later pass (scope/types/borrow/backend) ever sees an Expand:
        # each pack element's body copy is straight-line, per-element-typed, and
        # references the owned fan-out parameter args_i directly.
        substitutor = self.monomorphizer.substitutor
        pack_param_fanout: Dict[str, list] = {}
        for param in generic.params:
            pack = substitutor._pack_binding_for(param, substitution)
            if pack is not None:
                # STABLE Phase-0 convention: the i-th fan-out param is
                # f"{param.name}_{i}" (0-based, contiguous); arity 0 -> [].
                pack_param_fanout[param.name] = [
                    f"{param.name}_{i}" for i in range(len(pack.types))
                ]
        if pack_param_fanout:
            from sushi_lang.semantics.generics.monomorphize.unroll import unroll_expands
            concrete_body = unroll_expands(concrete_body, pack_param_fanout)

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
        from sushi_lang.semantics.ast import Program

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

            # Register the concrete function (FuncSig + program/unit append) through the
            # shared synthesis helper so the monomorphizer and lambda-lifting stay in sync.
            from sushi_lang.semantics.generics.synthesis import register_synthesized_function
            register_synthesized_function(
                self.monomorphizer.func_table,
                concrete_func,
                program=target_program if is_single_file else None,
                units=None if is_single_file else units,
            )

            # After monomorphizing, check if any new instantiations were discovered
            # and add them to the worklist
            worklist.update(self.monomorphizer.pending_instantiations)
            self.monomorphizer.pending_instantiations.clear()

    def _collect_nested_instantiations(
        self,
        body: 'Block',
        substitution: Dict[str, "Type | TypePack"],
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
        from sushi_lang.semantics.ast import Let, ExprStmt, Return, If, While, Match, Foreach, Block

        # Build variable type map from function parameters
        var_types = {}
        for param in generic_func.params:
            if param.ty:
                # A pack-typed value-parameter has no single scalar type: it
                # fans out into N concrete params at the signature level. Its
                # body usage (expand(...)) is a later phase, so it contributes
                # no entry to the scalar var-type map here (and routing it
                # through substitute_type would hit the scalar-position guard).
                if self.monomorphizer.substitutor._pack_binding_for(param, substitution) is not None:
                    continue
                # Substitute type parameters in parameter type
                concrete_ty = self.monomorphizer.substitutor.substitute_type(param.ty, substitution)
                var_types[param.name] = concrete_ty

        from sushi_lang.semantics.ast import Lambda

        for stmt in body.statements:
            if isinstance(stmt, Let) and stmt.value:
                self._collect_from_expr(stmt.value, substitution, var_types)
                # A block-body lambda (only ever a `let` RHS) has its statements walked here,
                # where generic_func is available to rebuild the nested scope.
                if isinstance(stmt.value, Lambda) and stmt.value.is_block_body:
                    self._collect_nested_instantiations(stmt.value.body, substitution, generic_func)
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

    def _collect_from_expr(self, expr, substitution: Dict[str, "Type | TypePack"], var_types: Dict[str, Type]) -> None:
        """Recursively scan expression for generic function calls.

        Args:
            expr: Expression to scan
            substitution: Type parameter substitution map
            var_types: Map from variable names to their concrete types
        """
        from sushi_lang.semantics.ast import (
            Call, Name, BinaryOp, UnaryOp, TryExpr, DotCall,
            IndexAccess, ArrayLiteral, EnumConstructor, CastExpr,
            InterpolatedString, Borrow, RangeExpr, Spread, MemberAccess,
            MethodCall, DynamicArrayFrom, DynamicArrayNew, BlankLit, Lambda,
            IntLit, FloatLit, StringLit, BoolLit,
        )

        if isinstance(expr, Call):
            if isinstance(expr.callee, Name):
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

            # Recurse into arguments: a generic call nested inside another call's argument
            # (e.g. f(g(x))) was missed, the monomorphizer's own #191 (issue #214).
            for arg in getattr(expr, "args", []) or []:
                self._collect_from_expr(arg, substitution, var_types)

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
        elif isinstance(expr, IndexAccess):
            self._collect_from_expr(expr.array, substitution, var_types)
            self._collect_from_expr(expr.index, substitution, var_types)
        elif isinstance(expr, ArrayLiteral):
            for element in expr.elements:
                self._collect_from_expr(element, substitution, var_types)
        elif isinstance(expr, EnumConstructor):
            for arg in expr.args:
                self._collect_from_expr(arg, substitution, var_types)
        elif isinstance(expr, CastExpr):
            self._collect_from_expr(expr.expr, substitution, var_types)
        elif isinstance(expr, InterpolatedString):
            for part in expr.parts:
                if not isinstance(part, str):
                    self._collect_from_expr(part, substitution, var_types)
        elif isinstance(expr, Borrow):
            self._collect_from_expr(expr.expr, substitution, var_types)
        elif isinstance(expr, RangeExpr):
            self._collect_from_expr(expr.start, substitution, var_types)
            self._collect_from_expr(expr.end, substitution, var_types)
        elif isinstance(expr, Spread):
            self._collect_from_expr(expr.value, substitution, var_types)
        elif isinstance(expr, MemberAccess):
            self._collect_from_expr(expr.receiver, substitution, var_types)
        elif isinstance(expr, MethodCall):
            self._collect_from_expr(expr.receiver, substitution, var_types)
            for arg in expr.args:
                self._collect_from_expr(arg, substitution, var_types)
        elif isinstance(expr, DynamicArrayFrom):
            self._collect_from_expr(expr.elements, substitution, var_types)
        elif isinstance(expr, Lambda):
            # An expression-body lambda scans directly. A block-body lambda (a `let` RHS) is
            # walked in _collect_nested_instantiations, which has the generic_func needed to
            # rebuild its var-type scope.
            if not expr.is_block_body:
                self._collect_from_expr(expr.body, substitution, var_types)
        elif isinstance(expr, (IntLit, FloatLit, StringLit, BoolLit, Name,
                               BlankLit, DynamicArrayNew)):
            # Leaf nodes: no nested expressions to scan.
            pass

    def _get_arg_inferrer(self, var_types: Dict[str, Type]):
        """Pass 2's TypeValidator over the whole program, seeded with this scope.

        Cached per FunctionMonomorphizer and re-seeded with the current `var_types` on each
        call (the scope changes per monomorphized function). Wired to discard diagnostics --
        they belong to Pass 2, which runs later. Returns None when no SymbolTables was
        supplied (unit-test paths), in which case the caller falls back to the var-type map.
        Mirrors the shared inferrer Pass 1.5 builds in instantiate/__init__.py.
        """
        tables = getattr(self.monomorphizer, "tables", None)
        if tables is None:
            return None
        inferrer = getattr(self, "_arg_inferrer", None)
        if inferrer is None:
            from sushi_lang.internals.report import Reporter
            from sushi_lang.semantics.passes.types import TypeValidator
            inferrer = TypeValidator(Reporter(), tables)
            self._arg_inferrer = inferrer
        # Share the current scope by reference so Name/self lookups resolve to concrete types.
        inferrer.variable_types = var_types
        return inferrer

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
        from sushi_lang.semantics.ast import Name
        from sushi_lang.semantics.typesys import UnknownType

        type_param_map = {}

        # Match each call argument to function parameter
        call_args = getattr(call, "args", []) or []
        if len(call_args) != len(generic_func.params):
            return None

        inferrer = self._get_arg_inferrer(var_types)

        for arg_expr, param in zip(call_args, generic_func.params):
            # Infer the argument's type through Pass 2's shared inferrer when available
            # (it types any expression: a call, cast, method result, or literal -- not just
            # a bare Name), falling back to the var-type map on the unit-test paths that have
            # no SymbolTables. The Names-only fallback used to abort inference on the FIRST
            # non-Name argument, dropping the whole instantiation even when a later Name
            # argument still supplied the type parameter (issue #214).
            arg_type = None
            if inferrer is not None:
                arg_type = inferrer.infer_expression_type(arg_expr)
            if arg_type is None and isinstance(arg_expr, Name) and arg_expr.id in var_types:
                arg_type = var_types[arg_expr.id]

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
        from sushi_lang.semantics.typesys import EnumType, StructType

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
