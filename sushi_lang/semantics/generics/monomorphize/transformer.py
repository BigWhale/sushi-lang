"""Type parameter substitution and AST transformation."""
from __future__ import annotations
from dataclasses import replace
from typing import Dict, List, TYPE_CHECKING
import copy

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    BlankLit, BoolLit, DynamicArrayNew, FloatLit, IntLit, Name, StringLit
)
from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter, TypePack
from sushi_lang.semantics.typesys import (
    Type, EnumType, EnumVariantInfo, StructType, UnknownType,
    PointerType, ArrayType, DynamicArrayType, ReferenceType
)

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import ArrayElement, Block, Expr, Lambda, Param


# Nodes that hold no sub-expression and no type the SOURCE writes. Listed EXPLICITLY so
# `substitute_expr`'s `case _` can be a hard error (CE0135) instead of a silent copy.
INERT_EXPRS = (Name, IntLit, FloatLit, BoolLit, BlankLit, StringLit, DynamicArrayNew)


class TypeSubstitutor:
    """Handles type parameter substitution in types and AST nodes."""

    def __init__(self, monomorphizer):
        """Initialize substitutor with reference to parent monomorphizer."""
        self.monomorphizer = monomorphizer

    def substitute_type(self, ty: Type, substitution: Dict[str, "Type | TypePack"]) -> Type:
        """Recursively substitute type parameters in a type."""
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
                if isinstance(result, GenericTypeRef):
                    return self.substitute_type(result, {})
                return result
            else:
                return ty

        if isinstance(ty, UnknownType):
            if ty.name in substitution:
                result = substitution[ty.name]
                # A pack binding cannot fill a single scalar type position (see above).
                if isinstance(result, TypePack):
                    raise ValueError(
                        f"type-pack '{ty.name}' used in a scalar type position; "
                        f"pack expansion happens at the parameter-list level"
                    )
                return result
            return ty

        if isinstance(ty, PointerType):
            return PointerType(
                pointee_type=self.substitute_type(ty.pointee_type, substitution)
            )

        # For reference types (peek T / poke T), substitute the referenced type,
        # keeping the mutability (F7, 2026-08-14). Without this arm a monomorphized
        # signature kept the literal `peek Pair@(A, B)` and every call failed CE2006.
        if isinstance(ty, ReferenceType):
            return ReferenceType(
                referenced_type=self.substitute_type(ty.referenced_type, substitution),
                mutability=ty.mutability,
            )

        if isinstance(ty, ArrayType):
            return ArrayType(
                base_type=self.substitute_type(ty.base_type, substitution),
                size=ty.size
            )
        elif isinstance(ty, DynamicArrayType):
            return DynamicArrayType(
                base_type=self.substitute_type(ty.base_type, substitution)
            )

        if isinstance(ty, StructType):
            new_fields = []
            for field_name, field_type in ty.fields:
                new_field_type = self.substitute_type(field_type, substitution)
                new_fields.append((field_name, new_field_type))
            return StructType(name=ty.name, fields=tuple(new_fields))

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

        if isinstance(ty, GenericTypeRef):
            new_type_args = []
            for arg in ty.type_args:
                new_arg = self.substitute_type(arg, substitution)
                new_type_args.append(new_arg)

            cache_key = (ty.base_name, tuple(new_type_args))
            if cache_key in self.monomorphizer.cache:
                return self.monomorphizer.cache[cache_key]

            if cache_key in self.monomorphizer.struct_cache:
                return self.monomorphizer.struct_cache[cache_key]

            if ty.base_name in self.monomorphizer.generic_enums:
                generic = self.monomorphizer.generic_enums[ty.base_name]
                concrete = self.monomorphizer.monomorphize_enum(generic, tuple(new_type_args))
                return concrete

            if ty.base_name in self.monomorphizer.generic_structs:
                generic = self.monomorphizer.generic_structs[ty.base_name]
                concrete = self.monomorphizer.monomorphize_struct(generic, tuple(new_type_args))
                return concrete

            return GenericTypeRef(
                base_name=ty.base_name,
                type_args=tuple(new_type_args)
            )

        # For function types (fn(T) -> U), substitute the parameter, ok, and err types.
        # `replace`, so both metadata fields ride along: `captures` drives ownership, and
        # `param_modes` carries the declared `nom` a rebuild used to drop (#368).
        from sushi_lang.semantics.typesys import FunctionType
        if isinstance(ty, FunctionType):
            return replace(
                ty,
                param_types=tuple(self.substitute_type(p, substitution) for p in ty.param_types),
                ok_type=self.substitute_type(ty.ok_type, substitution),
                err_type=self.substitute_type(ty.err_type, substitution),
            )

        return ty

    def _pack_binding_for(
        self, param: 'Param', substitution: Dict[str, "Type | TypePack"]
    ) -> 'TypePack | None':
        """The TypePack a value-parameter fans out to, or None if it is not pack-typed."""
        if isinstance(param.ty, (TypeParameter, UnknownType)):
            binding = substitution.get(param.ty.name)
            if isinstance(binding, TypePack):
                return binding
        return None

    def expand_pack_param(
        self, param: 'Param', substitution: Dict[str, "Type | TypePack"]
    ) -> List['Param']:
        """Fan a single value-parameter out into its concrete instantiation(s)."""
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
                    is_nom=getattr(param, 'is_nom', False),
                    nom_span=getattr(param, 'nom_span', None),
                )
                for i, element_type in enumerate(pack.types)
            ]

        concrete_type = self.substitute_type(param.ty, substitution) if param.ty else None
        return [
            Param(
                name=param.name,
                ty=concrete_type,
                name_span=param.name_span,
                type_span=param.type_span,
                loc=getattr(param, 'loc', None),
                is_variadic=getattr(param, 'is_variadic', False),
                # The MODE is declared, so it is the same for every instantiation
                # (docs/design/borrow-model.md S7). Dropping it here would make one
                # body's parameters transfer and another's borrow.
                is_nom=getattr(param, 'is_nom', False),
                nom_span=getattr(param, 'nom_span', None),
            )
        ]

    def substitute_body(self, body: 'Block', substitution: Dict[str, "Type | TypePack"]) -> 'Block':
        """Substitute type parameters in a function body."""
        new_statements = []
        for stmt in body.statements:
            new_stmt = self.substitute_statement(stmt, substitution)
            new_statements.append(new_stmt)

        result = copy.copy(body)
        result.statements = new_statements
        return result

    def substitute_statement(self, stmt, substitution: Dict[str, "Type | TypePack"]):
        """Recursively substitute types in a statement."""
        from sushi_lang.semantics.ast import (
            Let, Rebind, If, While, Foreach, Expand, Return, Match,
            ExprStmt, Block, Break, Continue, Print, PrintLn
        )

        if isinstance(stmt, Let):
            result = copy.copy(stmt)
            if stmt.ty:
                result.ty = self.substitute_type(stmt.ty, substitution)
            if stmt.value:
                result.value = self.substitute_expr(stmt.value, substitution)
            return result

        if isinstance(stmt, Rebind):
            result = copy.copy(stmt)
            result.target = self.substitute_expr(stmt.target, substitution)
            result.value = self.substitute_expr(stmt.value, substitution)
            return result

        if isinstance(stmt, If):
            result = copy.copy(stmt)
            result.arms = [
                (self.substitute_expr(cond, substitution), self.substitute_body(block, substitution))
                for cond, block in stmt.arms
            ]
            if stmt.else_block:
                result.else_block = self.substitute_body(stmt.else_block, substitution)
            return result

        if isinstance(stmt, While):
            result = copy.copy(stmt)
            result.cond = self.substitute_expr(stmt.cond, substitution)
            result.body = self.substitute_body(stmt.body, substitution)
            return result

        if isinstance(stmt, Foreach):
            result = copy.copy(stmt)
            result.iterable = self.substitute_expr(stmt.iterable, substitution)
            result.body = self.substitute_body(stmt.body, substitution)
            # The item ANNOTATION is source-written and names the type parameter as any
            # other annotation does (#602). The `??` binder's hidden `let` is stamped
            # from it by the typecheck pass, so each instantiation needs its own copy.
            result.item_type = self._substitute_optional_type(stmt.item_type, substitution)
            if stmt.item_try_let is not None:
                result.item_try_let = self.substitute_statement(stmt.item_try_let, substitution)
            return result

        # Expand statement (compile-time pack expansion). Type-substitute the
        # iterable and body here so the surviving Expand node is fully concrete;
        # the actual unrolling into ordinary statements is a dedicated post-pass
        # (unroll_expands) run after substitute_body, once the per-pack fan-out
        # parameter names are known.
        if isinstance(stmt, Expand):
            result = copy.copy(stmt)
            result.iterable = self.substitute_expr(stmt.iterable, substitution)
            result.body = self.substitute_body(stmt.body, substitution)
            return result

        if isinstance(stmt, (Print, PrintLn)):
            result = copy.copy(stmt)
            result.value = self.substitute_expr(stmt.value, substitution)
            return result

        if isinstance(stmt, Return):
            result = copy.copy(stmt)
            if stmt.value:
                result.value = self.substitute_expr(stmt.value, substitution)
            return result

        if isinstance(stmt, Match):
            result = copy.copy(stmt)
            if stmt.scrutinee:
                result.scrutinee = self.substitute_expr(stmt.scrutinee, substitution)
            new_arms = []
            for arm in stmt.arms:
                new_arm = copy.copy(arm)
                if isinstance(arm.body, Block):
                    new_arm.body = self.substitute_body(arm.body, substitution)
                else:
                    new_arm.body = self.substitute_expr(arm.body, substitution)
                new_arms.append(new_arm)
            result.arms = new_arms
            return result

        if isinstance(stmt, ExprStmt):
            result = copy.copy(stmt)
            result.expr = self.substitute_expr(stmt.expr, substitution)
            return result

        if isinstance(stmt, (Break, Continue)):
            return copy.copy(stmt)

        # The same backstop the expression walk carries: a statement with no arm keeps
        # every type parameter its annotations name.
        er.raise_internal_error("CE0135", kind="statement", node=type(stmt).__name__)

    def _substitute_optional_type(
        self, ty: 'Type | None', substitution: Dict[str, "Type | TypePack"]
    ) -> 'Type | None':
        """A type field the source may leave empty."""
        return self.substitute_type(ty, substitution) if ty is not None else None

    def _substitute_array_element(
        self, element: 'ArrayElement', substitution: Dict[str, "Type | TypePack"]
    ) -> 'ArrayElement':
        """One element of an array literal: its value, and the count of a run."""
        result = copy.copy(element)
        result.value = self.substitute_expr(element.value, substitution)
        if element.count is not None:
            result.count = self.substitute_expr(element.count, substitution)
        return result

    def _substitute_lambda(
        self, lam: 'Lambda', substitution: Dict[str, "Type | TypePack"]
    ) -> 'Lambda':
        """A lambda carries annotations of its own: each parameter, the return, the channel."""
        from sushi_lang.semantics.ast import Block

        result = copy.copy(lam)
        new_params = []
        for param in lam.params:
            new_param = copy.copy(param)
            new_param.ty = self._substitute_optional_type(param.ty, substitution)
            new_params.append(new_param)
        result.params = new_params
        result.ret = self._substitute_optional_type(lam.ret, substitution)
        result.err_type = self._substitute_optional_type(lam.err_type, substitution)
        if isinstance(lam.body, Block):
            result.body = self.substitute_body(lam.body, substitution)
        else:
            result.body = self.substitute_expr(lam.body, substitution)
        return result

    def substitute_expr(self, expr: 'Expr', substitution: Dict[str, "Type | TypePack"]) -> 'Expr':
        """Substitute types in an expression. Total over `Expr`; the `else` is a hard CE0135."""
        from sushi_lang.semantics.ast import (
            ArrayLiteral, BinaryOp, Borrow, Call, CastExpr, DotCall, DynamicArrayFrom,
            EnumConstructor, IndexAccess, InterpolatedString, Lambda, MemberAccess,
            MethodCall, RangeExpr, Spread, TryExpr, UnaryOp,
        )

        match expr:
            case CastExpr():
                result = copy.copy(expr)
                result.expr = self.substitute_expr(expr.expr, substitution)
                result.target_type = self.substitute_type(expr.target_type, substitution)
                return result
            case Call():
                result = copy.copy(expr)
                result.callee = self.substitute_expr(expr.callee, substitution)
                result.args = [self.substitute_expr(a, substitution) for a in expr.args]
                # Explicit call-site type arguments are written in the source, so they
                # name the enclosing type parameter as any annotation does (#602).
                if expr.type_args:
                    result.type_args = [
                        self.substitute_type(t, substitution) for t in expr.type_args
                    ]
                return result
            case DotCall():
                result = copy.copy(expr)
                result.receiver = self.substitute_expr(expr.receiver, substitution)
                result.args = [self.substitute_expr(a, substitution) for a in expr.args]
                if expr.type_args:
                    result.type_args = [
                        self.substitute_type(t, substitution) for t in expr.type_args
                    ]
                return result
            case MethodCall():
                result = copy.copy(expr)
                result.receiver = self.substitute_expr(expr.receiver, substitution)
                result.args = [self.substitute_expr(a, substitution) for a in expr.args]
                return result
            case MemberAccess():
                result = copy.copy(expr)
                result.receiver = self.substitute_expr(expr.receiver, substitution)
                return result
            case EnumConstructor():
                result = copy.copy(expr)
                result.args = [self.substitute_expr(a, substitution) for a in expr.args]
                return result
            case BinaryOp():
                result = copy.copy(expr)
                result.left = self.substitute_expr(expr.left, substitution)
                result.right = self.substitute_expr(expr.right, substitution)
                return result
            case UnaryOp() | Borrow() | TryExpr():
                result = copy.copy(expr)
                result.expr = self.substitute_expr(expr.expr, substitution)
                return result
            case IndexAccess():
                result = copy.copy(expr)
                result.array = self.substitute_expr(expr.array, substitution)
                result.index = self.substitute_expr(expr.index, substitution)
                return result
            case RangeExpr():
                result = copy.copy(expr)
                result.start = self.substitute_expr(expr.start, substitution)
                result.end = self.substitute_expr(expr.end, substitution)
                return result
            case Spread():
                result = copy.copy(expr)
                result.value = self.substitute_expr(expr.value, substitution)
                return result
            case InterpolatedString():
                result = copy.copy(expr)
                result.parts = [
                    part if isinstance(part, str)
                    else self.substitute_expr(part, substitution)
                    for part in expr.parts
                ]
                return result
            case ArrayLiteral():
                result = copy.copy(expr)
                result.elements = [
                    self._substitute_array_element(e, substitution) for e in expr.elements
                ]
                return result
            case DynamicArrayFrom():
                result = copy.copy(expr)
                result.elements = self.substitute_expr(expr.elements, substitution)
                return result
            case Lambda():
                return self._substitute_lambda(expr, substitution)
            case _ if isinstance(expr, INERT_EXPRS):
                return copy.copy(expr)
            case _:
                # NOT a silent copy: a node with no arm keeps its type PARAMETER, and the
                # compiler's own bookkeeping name reaches the user (#602). The CI gate is
                # tests/unit/test_substitution_dispatch_is_total.py; this is the backstop.
                er.raise_internal_error("CE0135", kind="expression", node=type(expr).__name__)
