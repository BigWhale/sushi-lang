"""Generic function monomorphization."""
from __future__ import annotations
from typing import Dict, Tuple, Set, Optional, TYPE_CHECKING
import copy

from sushi_lang.semantics.generics.name_mangling import mangle_function_name
from sushi_lang.semantics.generics.types import TypePack
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Block, Call, FuncDef
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef


class FunctionMonomorphizer:
    """Handles monomorphization of generic functions."""

    def __init__(self, monomorphizer):
        """Initialize function monomorphizer."""
        self.monomorphizer = monomorphizer

    def build_substitution(
        self,
        generic: 'GenericFuncDef',
        type_args: Tuple[Type, ...]
    ) -> Dict[str, "Type | TypePack"]:
        """Build the type-parameter -> binding substitution map for a generic."""
        tps = list(generic.type_params)

        pack_indices = [i for i, tp in enumerate(tps) if getattr(tp, 'is_pack', False)]

        if not pack_indices:
            if len(type_args) != len(generic.type_params):
                raise ValueError(
                    f"Type argument count mismatch: {generic.name} expects "
                    f"{len(generic.type_params)} args, got {len(type_args)}"
                )

            self.monomorphizer._validate_type_constraints(generic.type_params, type_args)

            substitution: Dict[str, "Type | TypePack"] = {}
            for param, arg in zip(generic.type_params, type_args, strict=False):
                param_name = param.name if hasattr(param, 'name') else str(param)
                substitution[param_name] = arg
            return substitution

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

        self.monomorphizer._validate_type_constraints(leading_params, leading_args)

        substitution = {}
        for param, arg in zip(leading_params, leading_args, strict=False):
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
        """Create concrete function from generic definition."""
        cache_key = (generic.name, type_args)
        if cache_key in self.monomorphizer.func_cache:
            return self.monomorphizer.func_cache[cache_key]

        substitution = self.build_substitution(generic, type_args)

        # Substitute in parameter types. A pack-typed value-parameter fans out
        # into N concrete params (one per pack element, possibly zero); a normal
        # param yields exactly one concrete param identical to the legacy result.
        concrete_params = []
        for param in generic.params:
            concrete_params.extend(
                self.monomorphizer.substitutor.expand_pack_param(param, substitution)
            )

        concrete_ret = self.monomorphizer.substitutor.substitute_type(
            generic.ret, substitution
        ) if generic.ret else None

        # A trailing pack type-param passes its arity, so the symbol is distinct per pack
        # size and cannot collide with a regular generic of the same base.
        type_params = generic.type_params or []
        has_pack = bool(type_params) and getattr(type_params[-1], 'is_pack', False)
        if has_pack:
            pack_arity = len(type_args) - (len(type_params) - 1)
            mangled_name = mangle_function_name(
                generic.name, type_args, pack_arity=pack_arity
            )
        else:
            mangled_name = mangle_function_name(generic.name, type_args)

        self.monomorphizer.monomorphized_functions[mangled_name] = (generic.name, type_args)

        self._collect_nested_instantiations(generic.body, substitution, generic)

        concrete_body = self.monomorphizer.substitutor.substitute_body(generic.body, substitution)

        # Unroll `expand(...)` into ordinary statements, so no later pass ever sees an
        # Expand: each element's copy is straight-line and references args_i directly.
        substitutor = self.monomorphizer.substitutor
        pack_param_fanout: Dict[str, list] = {}
        for param in generic.params:
            pack = substitutor._pack_binding_for(param, substitution)
            if pack is not None:
                pack_param_fanout[param.name] = [
                    f"{param.name}_{i}" for i in range(len(pack.types))
                ]
        if pack_param_fanout:
            from sushi_lang.semantics.generics.monomorphize.unroll import unroll_expands
            concrete_body = unroll_expands(concrete_body, pack_param_fanout)

        concrete_func = copy.copy(generic)
        concrete_func.name = mangled_name
        concrete_func.params = concrete_params
        concrete_func.ret = concrete_ret
        concrete_func.body = concrete_body
        concrete_func.type_params = None  # No longer generic

        self.monomorphizer.func_cache[cache_key] = concrete_func

        return concrete_func

    def monomorphize_all_functions(
        self,
        function_instantiations: Set[Tuple[str, Tuple[Type, ...]]],
        program_or_units
    ) -> None:
        """Monomorphize all detected function instantiations."""
        from sushi_lang.semantics.ast import Program

        if not self.monomorphizer.func_table:
            return

        is_single_file = isinstance(program_or_units, Program)
        target_program = program_or_units if is_single_file else None
        units = None if is_single_file else program_or_units

        worklist = set(function_instantiations)
        processed = set()

        self.monomorphizer.pending_instantiations = set()

        while worklist:
            func_name, type_args = worklist.pop()

            if (func_name, type_args) in processed:
                continue
            processed.add((func_name, type_args))

            if func_name not in self.monomorphizer.generic_funcs:
                continue

            generic_func = self.monomorphizer.generic_funcs[func_name]

            concrete_func = self.monomorphize_function(generic_func, type_args)

            # Extract enum/struct instantiations from the function signature
            # This ensures that Result<T>, Maybe<T>, and other generic return/param types
            # are properly monomorphized even if they weren't detected by InstantiationCollector
            signature_instantiations = set()

            if concrete_func.ret:
                if hasattr(concrete_func, 'err_type') and concrete_func.err_type:
                    err_type = concrete_func.err_type
                else:
                    err_type = self.monomorphizer.enum_table.by_name.get("StdError") if self.monomorphizer.enum_table else None

                if err_type:
                    result_type_args = (concrete_func.ret, err_type)
                    signature_instantiations.add(("Result", result_type_args))

            self._extract_type_instantiations(concrete_func.ret, signature_instantiations)
            for param in concrete_func.params:
                self._extract_type_instantiations(param.ty, signature_instantiations)

            for base_name, sig_type_args in signature_instantiations:
                if base_name in self.monomorphizer.generic_enums:
                    concrete_enum = self.monomorphizer.monomorphize_enum(
                        self.monomorphizer.generic_enums[base_name], sig_type_args
                    )
                    if self.monomorphizer.enum_table and concrete_enum.name not in self.monomorphizer.enum_table.by_name:
                        self.monomorphizer.enum_table.by_name[concrete_enum.name] = concrete_enum
                        self.monomorphizer.enum_table.order.append(concrete_enum.name)
                elif base_name in self.monomorphizer.generic_structs:
                    concrete_struct = self.monomorphizer.monomorphize_struct(
                        self.monomorphizer.generic_structs[base_name], sig_type_args
                    )
                    if self.monomorphizer.struct_table and concrete_struct.name not in self.monomorphizer.struct_table.by_name:
                        self.monomorphizer.struct_table.by_name[concrete_struct.name] = concrete_struct
                        self.monomorphizer.struct_table.order.append(concrete_struct.name)

            mangled_name = concrete_func.name

            if mangled_name in self.monomorphizer.func_table.by_name:
                continue

            from sushi_lang.semantics.generics.synthesis import register_synthesized_function
            register_synthesized_function(
                self.monomorphizer.func_table,
                concrete_func,
                program=target_program if is_single_file else None,
                units=None if is_single_file else units,
            )

            worklist.update(self.monomorphizer.pending_instantiations)
            self.monomorphizer.pending_instantiations.clear()

    def _collect_nested_instantiations(
        self,
        body: 'Block',
        substitution: Dict[str, "Type | TypePack"],
        generic_func: 'GenericFuncDef'
    ) -> None:
        """Scan function body for calls to other generic functions and recursively monomorphize
        them.
        """
        from sushi_lang.semantics.ast import Let, ExprStmt, Return, If, While, Match, Foreach, Block

        var_types = {}
        for param in generic_func.params:
            if param.ty:
                # A pack-typed value-parameter fans out into N concrete params, so it has
                # no single scalar type and contributes no entry here.
                if self.monomorphizer.substitutor._pack_binding_for(param, substitution) is not None:
                    continue
                concrete_ty = self.monomorphizer.substitutor.substitute_type(param.ty, substitution)
                var_types[param.name] = concrete_ty

        from sushi_lang.semantics.ast import Lambda

        for stmt in body.statements:
            if isinstance(stmt, Let) and stmt.value:
                self._collect_from_expr(stmt.value, substitution, var_types)
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
        """Recursively scan expression for generic function calls."""
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

                if self.monomorphizer.generic_funcs and function_name in self.monomorphizer.generic_funcs:
                    generic_func = self.monomorphizer.generic_funcs[function_name]

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
            pass

    def _get_arg_inferrer(self, var_types: Dict[str, Type]):
        """Pass 2's TypeValidator over the whole program, seeded with this scope."""
        tables = getattr(self.monomorphizer, "tables", None)
        if tables is None:
            return None
        inferrer = getattr(self, "_arg_inferrer", None)
        if inferrer is None:
            from sushi_lang.internals.report import Reporter
            from sushi_lang.semantics.passes.types import TypeValidator
            inferrer = TypeValidator(Reporter(), tables)
            self._arg_inferrer = inferrer
        inferrer.variable_types = var_types
        return inferrer

    def _infer_type_args_with_substitution(
        self,
        call: 'Call',
        generic_func: 'GenericFuncDef',
        var_types: Dict[str, Type]
    ) -> Optional[Tuple[Type, ...]]:
        """Infer type arguments for a generic function call inside another generic function."""
        from sushi_lang.semantics.ast import Name
        from sushi_lang.semantics.generics.unify import unify_types

        type_param_map = {}

        call_args = getattr(call, "args", []) or []
        if len(call_args) != len(generic_func.params):
            return None

        inferrer = self._get_arg_inferrer(var_types)

        for arg_expr, param in zip(call_args, generic_func.params, strict=False):
            # Pass 2's shared inferrer types any expression, not just a bare Name. The
            # var-type map is the fallback for unit-test paths with no SymbolTables; a
            # Names-only walk aborted on the FIRST non-Name argument
            # argument still supplied the type parameter (issue #214).
            arg_type = None
            if inferrer is not None:
                arg_type = inferrer.infer_expression_type(arg_expr)
            if arg_type is None and isinstance(arg_expr, Name) and arg_expr.id in var_types:
                arg_type = var_types[arg_expr.id]

            if arg_type is None:
                return None

            # Unify with parameter type through the SHARED engine (F7, 2026-08-14).
            # A private UnknownType-or-exact-equality unification lived here before,
            # which is the two-spellings disease: it could not see through a `peek T`
            # parameter, so a borrowed nested call (`tag(peek v)`) inside a generic
            # body was never collected.
            if param.ty is None:
                return None
            if not unify_types(param.ty, arg_type, type_param_map):
                return None

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
        """Recursively extract all generic type instantiations from a Type."""
        from sushi_lang.semantics.typesys import EnumType, StructType

        if ty is None:
            return

        if isinstance(ty, EnumType) and hasattr(ty, 'generic_base') and ty.generic_base:
            base_name = ty.generic_base
            type_args = ty.generic_args if hasattr(ty, 'generic_args') and ty.generic_args else tuple()
            if type_args:  # Only add if we have type arguments
                instantiations.add((base_name, type_args))
                for arg in type_args:
                    self._extract_type_instantiations(arg, instantiations)

        elif isinstance(ty, StructType) and hasattr(ty, 'generic_base') and ty.generic_base:
            base_name = ty.generic_base
            type_args = ty.generic_args if hasattr(ty, 'generic_args') and ty.generic_args else tuple()
            if type_args:  # Only add if we have type arguments
                instantiations.add((base_name, type_args))
                for arg in type_args:
                    self._extract_type_instantiations(arg, instantiations)

        elif hasattr(ty, 'element_type'):
            self._extract_type_instantiations(ty.element_type, instantiations)

        elif hasattr(ty, 'target_type'):
            self._extract_type_instantiations(ty.target_type, instantiations)
