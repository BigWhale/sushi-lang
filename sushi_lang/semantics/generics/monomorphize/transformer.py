# semantics/generics/monomorphize/transformer.py
"""
Type parameter substitution and AST transformation.

This module handles the core substitution logic for monomorphization:
- Recursively substituting type parameters with concrete types
- Deep copying function bodies for each monomorphized instance
- Type-aware substitution with copy-on-write for immutable types
"""
from __future__ import annotations
from typing import Dict, List, TYPE_CHECKING
import copy

from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter, TypePack
from sushi_lang.semantics.typesys import (
    Type, EnumType, EnumVariantInfo, StructType, UnknownType,
    PointerType, ArrayType, DynamicArrayType
)

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Block, Param


class TypeSubstitutor:
    """Handles type parameter substitution in types and AST nodes.

    This class provides methods for recursively substituting type parameters
    with concrete types throughout the AST. It handles:
    - Simple type substitution (T → i32)
    - Nested generic types (Result<Maybe<T>> → Result<Maybe<i32>>)
    - Array and pointer types
    - Function bodies (statements and expressions)

    Type objects use copy-on-write (types are immutable after substitution).
    AST nodes (blocks, statements, expressions) are always copied because
    they may be annotated with resolved types during later compiler passes.
    """

    def __init__(self, monomorphizer):
        """Initialize substitutor with reference to parent monomorphizer.

        Args:
            monomorphizer: Parent Monomorphizer instance (needed for recursive
                           monomorphization of nested generics)
        """
        self.monomorphizer = monomorphizer

    def substitute_type(self, ty: Type, substitution: Dict[str, "Type | TypePack"]) -> Type:
        """Recursively substitute type parameters in a type.

        Args:
            ty: The type to substitute in (may contain TypeParameter or UnknownType
                instances representing type parameters)
            substitution: Map from type parameter names to concrete types (or a
                ``TypePack`` for pack bindings; see the scalar-position guard below)

        Returns:
            Type with all TypeParameters replaced by concrete types
        """
        # If this is a type parameter, substitute it
        if isinstance(ty, TypeParameter):
            if ty.name in substitution:
                result = substitution[ty.name]
                # A pack binding cannot fill a single scalar type position; the
                # position-level fan-out is handled at the parameter-list level
                # (later phase), not here.
                if isinstance(result, TypePack):
                    raise ValueError(
                        f"type-pack '{ty.name}' used in a scalar type position; "
                        f"pack expansion happens at the parameter-list level"
                    )
                # If the result is a GenericTypeRef, recursively resolve it to an EnumType
                if isinstance(result, GenericTypeRef):
                    return self.substitute_type(result, {})
                return result
            else:
                # Unknown type parameter - shouldn't happen, but be defensive
                return ty

        # Handle UnknownType that represents a type parameter (e.g., UnknownType("T"))
        # This happens when the AST builder creates UnknownType for type parameter references
        if isinstance(ty, UnknownType):
            if ty.name in substitution:
                result = substitution[ty.name]
                # A pack binding cannot fill a single scalar type position (see above).
                if isinstance(result, TypePack):
                    raise ValueError(
                        f"type-pack '{ty.name}' used in a scalar type position; "
                        f"pack expansion happens at the parameter-list level"
                    )
                # This UnknownType is actually a type parameter reference
                return result
            # Otherwise, it's a real unknown type (struct/enum) - pass through
            return ty

        # For pointer types, substitute the pointee type
        if isinstance(ty, PointerType):
            return PointerType(
                pointee_type=self.substitute_type(ty.pointee_type, substitution)
            )

        # For array types, substitute the element type
        if isinstance(ty, ArrayType):
            return ArrayType(
                base_type=self.substitute_type(ty.base_type, substitution),
                size=ty.size
            )
        elif isinstance(ty, DynamicArrayType):
            return DynamicArrayType(
                base_type=self.substitute_type(ty.base_type, substitution)
            )

        # For struct types, substitute field types
        if isinstance(ty, StructType):
            new_fields = []
            for field_name, field_type in ty.fields:
                new_field_type = self.substitute_type(field_type, substitution)
                new_fields.append((field_name, new_field_type))
            return StructType(name=ty.name, fields=tuple(new_fields))

        # For enum types, substitute variant associated types
        if isinstance(ty, EnumType):
            new_variants = []
            for variant in ty.variants:
                new_assoc_types = []
                for assoc_type in variant.associated_types:
                    new_assoc_type = self.substitute_type(assoc_type, substitution)
                    new_assoc_types.append(new_assoc_type)
                new_variants.append(EnumVariantInfo(
                    name=variant.name,
                    associated_types=tuple(new_assoc_types)
                ))
            return EnumType(name=ty.name, variants=tuple(new_variants))

        # For GenericTypeRef, recursively substitute type arguments and resolve to concrete type
        if isinstance(ty, GenericTypeRef):
            # First, recursively substitute any type parameters in the type arguments
            new_type_args = []
            for arg in ty.type_args:
                new_arg = self.substitute_type(arg, substitution)
                new_type_args.append(new_arg)

            # Check if we've already monomorphized this generic enum
            # This handles nested generics like Result<Either<i32, string>>
            cache_key = (ty.base_name, tuple(new_type_args))
            if cache_key in self.monomorphizer.cache:
                # Already monomorphized - return the concrete EnumType
                return self.monomorphizer.cache[cache_key]

            # Check if we've already monomorphized this generic struct
            # This handles nested generics like Box<Box<i32>>
            if cache_key in self.monomorphizer.struct_cache:
                # Already monomorphized - return the concrete StructType
                return self.monomorphizer.struct_cache[cache_key]

            # If not in cache, recursively monomorphize it now
            # This ensures nested generics are fully resolved
            if ty.base_name in self.monomorphizer.generic_enums:
                generic = self.monomorphizer.generic_enums[ty.base_name]
                concrete = self.monomorphizer.monomorphize_enum(generic, tuple(new_type_args))
                return concrete

            # Check if it's a generic struct
            if ty.base_name in self.monomorphizer.generic_structs:
                generic = self.monomorphizer.generic_structs[ty.base_name]
                concrete = self.monomorphizer.monomorphize_struct(generic, tuple(new_type_args))
                return concrete

            # If it's not a known generic enum or struct, keep as GenericTypeRef
            # (this shouldn't happen for well-formed programs)
            return GenericTypeRef(
                base_name=ty.base_name,
                type_args=tuple(new_type_args)
            )

        # For all other types (BuiltinType, etc.), return as-is
        return ty

    def _pack_binding_for(
        self, param: 'Param', substitution: Dict[str, "Type | TypePack"]
    ) -> 'TypePack | None':
        """The TypePack a value-parameter fans out to, or None if it is not pack-typed.

        A value-parameter is pack-typed iff its declared type is a bare type-parameter
        reference (TypeParameter/UnknownType) whose name is bound to a TypePack in the
        substitution map. Shared by expand_pack_param (signature fan-out) and the
        monomorphizer's nested-instantiation scan (which must skip pack-typed params so
        they never reach the scalar-position guard in substitute_type).
        """
        if isinstance(param.ty, (TypeParameter, UnknownType)):
            binding = substitution.get(param.ty.name)
            if isinstance(binding, TypePack):
                return binding
        return None

    def expand_pack_param(
        self, param: 'Param', substitution: Dict[str, "Type | TypePack"]
    ) -> List['Param']:
        """Fan a single value-parameter out into its concrete instantiation(s).

        A value-parameter is *pack-typed* iff its declared type is a bare
        type-parameter reference (``TypeParameter`` or ``UnknownType``) whose
        name is bound to a ``TypePack`` in ``substitution``. Such a parameter
        conceptually stands for ``Ts... args`` and expands, position-wise, into
        one concrete parameter per pack element.

        Naming convention (STABLE CONTRACT for later phases): the i-th expanded
        parameter (0-based) is named ``f"{param.name}_{i}"``. An arity-0 pack
        therefore produces NO parameters at all (the position vanishes), and an
        arity-N pack produces ``args_0 .. args_{N-1}``. Phase 1's ``expand(...)``
        body lowering relies on this deterministic per-index naming to bind the
        expanded parameters back to elements of the pack.

        Each expanded parameter carries the original's ``name_span``,
        ``type_span`` and ``loc``, uses the already-concrete element type
        directly (it is NOT routed back through ``substitute_type``), and is an
        ordinary param (``is_variadic=False``).

        Non-pack parameters (the overwhelmingly common case) are returned as a
        single-element list whose one ``Param`` is byte-identical to what the
        legacy param loop produced: ``ty`` substituted via ``substitute_type``,
        every span/loc copied, and ``is_variadic`` preserved.

        Args:
            param: The original generic value-parameter.
            substitution: Type-parameter binding map (may contain ``TypePack``).

        Returns:
            A list of concrete ``Param`` nodes (length 0..N for a pack-typed
            param; exactly 1 otherwise).
        """
        from sushi_lang.semantics.ast import Param

        # Detect a pack-typed parameter: a bare type-param reference bound to a
        # TypePack. The expansion happens HERE, before substitute_type is ever
        # called on the pack name (which would hit the scalar-position guard).
        pack = self._pack_binding_for(param, substitution)
        if pack is not None:
            return [
                Param(
                    name=f"{param.name}_{i}",
                    ty=element_type,
                    name_span=param.name_span,
                    type_span=param.type_span,
                    loc=getattr(param, 'loc', None),
                    is_variadic=False,
                    # Mark each fan-out element as pack-derived so later passes can
                    # treat it specially (e.g. the scope pass must not emit a spurious
                    # CW1001 unused-variable warning: until expand(...) lands (T7b) the
                    # only way to consume these synthesized params is unavailable, and
                    # they carry user-invisible names like args_0/args_1).
                    is_pack=True,
                )
                for i, element_type in enumerate(pack.types)
            ]

        # Normal (non-pack) parameter: reproduce the legacy single-param result.
        concrete_type = self.substitute_type(param.ty, substitution) if param.ty else None
        return [
            Param(
                name=param.name,
                ty=concrete_type,
                name_span=param.name_span,
                type_span=param.type_span,
                loc=getattr(param, 'loc', None),
                is_variadic=getattr(param, 'is_variadic', False),
            )
        ]

    def substitute_body(self, body: 'Block', substitution: Dict[str, "Type | TypePack"]) -> 'Block':
        """Substitute type parameters in a function body.

        Always creates a new Block for each monomorphized function.
        This is necessary because each concrete function needs its own AST
        structure that may be annotated differently during later passes.

        Args:
            body: Original function body
            substitution: Map from type parameter names to concrete types

        Returns:
            New Block with substituted types
        """
        # Process all statements
        new_statements = []
        for stmt in body.statements:
            new_stmt = self.substitute_statement(stmt, substitution)
            new_statements.append(new_stmt)

        # Always create a new Block for the monomorphized function
        result = copy.copy(body)
        result.statements = new_statements
        return result

    def substitute_statement(self, stmt, substitution: Dict[str, "Type | TypePack"]):
        """Recursively substitute types in a statement.

        Always creates a new statement for each monomorphized function.
        This is necessary because expressions within statements may be
        annotated with resolved types during later passes.

        Args:
            stmt: Statement AST node
            substitution: Type substitution map

        Returns:
            New statement with substituted types
        """
        from sushi_lang.semantics.ast import (
            Let, Rebind, If, While, Foreach, Return, Match,
            ExprStmt, Block, Break, Continue, Print, PrintLn
        )

        # Let statement - substitute type annotation
        if isinstance(stmt, Let):
            result = copy.copy(stmt)
            if stmt.ty:
                result.ty = self.substitute_type(stmt.ty, substitution)
            if stmt.value:
                result.value = self.substitute_expr(stmt.value, substitution)
            return result

        # Rebind statement (assignment)
        if isinstance(stmt, Rebind):
            result = copy.copy(stmt)
            result.target = self.substitute_expr(stmt.target, substitution)
            result.value = self.substitute_expr(stmt.value, substitution)
            return result

        # If statement
        if isinstance(stmt, If):
            result = copy.copy(stmt)
            result.arms = [
                (self.substitute_expr(cond, substitution), self.substitute_body(block, substitution))
                for cond, block in stmt.arms
            ]
            if stmt.else_block:
                result.else_block = self.substitute_body(stmt.else_block, substitution)
            return result

        # While statement
        if isinstance(stmt, While):
            result = copy.copy(stmt)
            result.cond = self.substitute_expr(stmt.cond, substitution)
            result.body = self.substitute_body(stmt.body, substitution)
            return result

        # Foreach statement
        if isinstance(stmt, Foreach):
            result = copy.copy(stmt)
            result.iterable = self.substitute_expr(stmt.iterable, substitution)
            result.body = self.substitute_body(stmt.body, substitution)
            return result

        # Print/PrintLn statements
        if isinstance(stmt, (Print, PrintLn)):
            result = copy.copy(stmt)
            result.value = self.substitute_expr(stmt.value, substitution)
            return result

        # Return statement
        if isinstance(stmt, Return):
            result = copy.copy(stmt)
            if stmt.value:
                result.value = self.substitute_expr(stmt.value, substitution)
            return result

        # Match statement
        if isinstance(stmt, Match):
            result = copy.copy(stmt)
            if stmt.scrutinee:
                result.scrutinee = self.substitute_expr(stmt.scrutinee, substitution)
            new_arms = []
            for arm in stmt.arms:
                new_arm = copy.copy(arm)
                # Substitute body (can be Expr or Block)
                if isinstance(arm.body, Block):
                    new_arm.body = self.substitute_body(arm.body, substitution)
                else:
                    new_arm.body = self.substitute_expr(arm.body, substitution)
                new_arms.append(new_arm)
            result.arms = new_arms
            return result

        # Expression statement
        if isinstance(stmt, ExprStmt):
            result = copy.copy(stmt)
            result.expr = self.substitute_expr(stmt.expr, substitution)
            return result

        # Break/Continue - copy for unique instances
        if isinstance(stmt, (Break, Continue)):
            return copy.copy(stmt)

        # Unknown statement type - fallback to deep copy (conservative)
        return copy.deepcopy(stmt)

    def substitute_expr(self, expr, substitution: Dict[str, "Type | TypePack"]):
        """Recursively substitute types in an expression.

        Always creates new expression nodes because expressions may be annotated
        with resolved types during later passes (e.g., resolved_enum_type).
        Uses copy.copy for composite expressions and copy.deepcopy as fallback.

        Type substitution only affects CastExpr nodes which have explicit type
        annotations; other expressions are copied without modification.

        Args:
            expr: Expression AST node
            substitution: Type substitution map

        Returns:
            New expression with substituted types
        """
        from sushi_lang.semantics.ast import CastExpr, TryExpr

        # Cast expression - IMPORTANT: substitute target type
        if isinstance(expr, CastExpr):
            new_expr = self.substitute_expr(expr.expr, substitution)
            new_target_type = self.substitute_type(expr.target_type, substitution)
            result = copy.copy(expr)
            result.expr = new_expr
            result.target_type = new_target_type
            return result

        # Try expression (error propagation x??)
        if isinstance(expr, TryExpr):
            new_expr = self.substitute_expr(expr.expr, substitution)
            result = copy.copy(expr)
            result.expr = new_expr
            return result

        # For all other expressions, deep copy is sufficient
        # Expressions like Name, BinaryOp, Call, etc. don't contain type annotations
        return copy.deepcopy(expr)
