"""Function-level instantiation collection."""
from __future__ import annotations
from typing import TYPE_CHECKING, Set, Tuple

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.generics.instantiate.expressions import ExpressionScanner

from sushi_lang.semantics.generics.types import GenericTypeRef


class FunctionCollector:
    """Collects generic instantiations from function-level constructs."""

    def __init__(
        self,
        expression_scanner: "ExpressionScanner",
        instantiations: Set[Tuple[str, Tuple["Type", ...]]],
        variable_types: dict[str, "Type"],
        visited_types: Set[str],
    ):
        """Initialize function collector."""
        self.expression_scanner = expression_scanner
        self.instantiations = instantiations
        self.variable_types = variable_types
        self.visited_types = visited_types

    def _reset_scope(self) -> None:
        """Clear the per-function variable scope in place."""
        self.variable_types.clear()

    def _resolve_local_type(self, ty):
        """Resolve a bare struct/enum name (UnknownType) to its concrete type."""
        from sushi_lang.semantics.typesys import UnknownType
        if not isinstance(ty, UnknownType):
            return ty
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        structs = self.expression_scanner.type_inferrer.struct_table or {}
        enums = self.expression_scanner.type_inferrer.enum_table or {}
        resolved = resolve_unknown_type(ty, structs, enums)
        return resolved if resolved is not None else ty

    def _bind_self(self, target_type) -> None:
        """Bind `self` to the receiver type, mirroring the typecheck pass (signatures.py)."""
        from sushi_lang.semantics.typesys import (
            BuiltinType, ArrayType, DynamicArrayType, EnumType, StructType, UnknownType,
        )
        if target_type is None:
            return
        if isinstance(target_type, (BuiltinType, ArrayType, DynamicArrayType, EnumType, StructType)):
            self.variable_types["self"] = target_type
        elif isinstance(target_type, UnknownType):
            from sushi_lang.semantics.type_resolution import resolve_unknown_type
            structs = self.expression_scanner.type_inferrer.struct_table or {}
            enums = self.expression_scanner.type_inferrer.enum_table or {}
            resolved = resolve_unknown_type(target_type, structs, enums)
            if resolved is not None and resolved != target_type:
                self.variable_types["self"] = resolved

    def _infer_scrutinee_type(self, scrutinee):
        """The scrutinee's type, through the typecheck pass's own inferrer."""
        if scrutinee is None:
            return None
        validator = getattr(self.expression_scanner, "type_validator", None)
        if validator is None:
            return None
        try:
            return validator.infer_expression_type(scrutinee)
        except Exception:
            # This pass reports nothing: a scrutinee it cannot type simply collects no
            # binding, and the typecheck pass raises the real diagnostic afterwards.
            return None

    def _variant_payload_types(self, scrutinee_type, variant_name):
        """The payload types of one variant, or () when they cannot be read here.

        Two shapes reach this. A monomorphized `EnumType` carries its payload types
        already substituted. A `GenericTypeRef` does not -- the interned instance does not
        exist until the monomorphize pass -- so the generic TEMPLATE is read and its type
        parameters are substituted with the reference's own arguments. That keeps the rule
        general: `Result` and `Maybe` take the same path as a user's generic enum.
        """
        from sushi_lang.semantics.typesys import EnumType

        if isinstance(scrutinee_type, EnumType):
            for variant in scrutinee_type.variants:
                if variant.name == variant_name:
                    return tuple(variant.associated_types or ())
            return ()

        if isinstance(scrutinee_type, GenericTypeRef):
            tables = getattr(self.expression_scanner, "generic_enums", None)
            if tables is None:
                return ()
            template = tables.get(scrutinee_type.base_name)
            if template is None:
                return ()
            inferrer = self.expression_scanner.type_inferrer
            for variant in template.variants:
                if variant.name == variant_name:
                    return tuple(
                        inferrer.substitute_type_simple(
                            payload, template.type_params, scrutinee_type.type_args
                        )
                        for payload in (variant.associated_types or ())
                    )
            return ()

        return ()

    def _bind_pattern_payloads(self, pattern, scrutinee_type) -> list[tuple[str, "Type | None"]]:
        """Record an arm's payload bindings, and answer what to put back afterwards.

        A binding lives for its own arm only, so the previous value of every name touched
        is returned rather than the scope being cleared -- an arm must not see the arm
        before it, and it must not lose an outer local of the same name.
        """
        from sushi_lang.semantics.ast import Pattern, NomBinding

        if not isinstance(pattern, Pattern) or scrutinee_type is None:
            return []

        payloads = self._variant_payload_types(scrutinee_type, pattern.variant_name)
        if not payloads:
            return []

        saved: list[tuple[str, "Type | None"]] = []
        for binding, raw_payload in zip(pattern.bindings, payloads, strict=False):
            # Resolve before binding, exactly as a `let` local's annotation is resolved. A
            # template's payload can be a bare name -- an UnknownType("NetError") displays
            # as "NetError" while the enum table holds the real EnumType -- and binding the
            # unresolved one interns a Result whose element differs from the canonical
            # instance, which is the poisoned intern CE0126 reports with two identical
            # spellings.
            payload = self._resolve_local_type(raw_payload)
            if isinstance(binding, str):
                if binding == "_":
                    continue
                saved.append((binding, self.variable_types.get(binding)))
                self.variable_types[binding] = payload
            elif isinstance(binding, NomBinding):
                # A taken payload is the arm's own VALUE, so its type is the payload's --
                # no reference wrapper (borrow-model.md S10b).
                saved.append((binding.name, self.variable_types.get(binding.name)))
                self.variable_types[binding.name] = payload
            elif isinstance(binding, Pattern):
                saved.extend(self._bind_pattern_payloads(binding, payload))
            # A `peek`/`poke` RefBinding carries a ReferenceType rather than the payload's
            # own type, and a reference is not a type argument a generic can be called
            # with, so nothing is recorded for one.
        return saved

    def _unbind(self, saved: list[tuple[str, "Type | None"]]) -> None:
        """Put back what an arm's bindings displaced."""
        for name, previous in reversed(saved):
            if previous is None:
                self.variable_types.pop(name, None)
            else:
                self.variable_types[name] = previous

    def collect_from_function(self, func) -> None:
        """Collect generic instantiations from function signature and body."""
        if hasattr(func, 'type_params') and func.type_params:
            return

        self._reset_scope()

        # Collect from return type
        # IMPORTANT: All functions implicitly return Result<T, E>, so we need to
        # record Result<T, E> instantiation for the function's return type
        if func.ret is not None:
            self._collect_from_type(func.ret)
            from sushi_lang.semantics.typesys import GenericTypeRef as GTypeRef, UnknownType
            if not (isinstance(func.ret, GTypeRef) and func.ret.base_name == "Result"):
                std_error_ref = UnknownType("StdError")
                result_instantiation = GenericTypeRef(base_name="Result", type_args=(func.ret, std_error_ref))
                self._collect_from_type(result_instantiation)

        for param in func.params:
            self._collect_from_param(param)

        self._collect_from_block(func.body)

    def collect_from_extension(self, ext) -> None:
        """Collect generic instantiations from extension method signature and body."""
        self._reset_scope()
        self._bind_self(ext.target_type)

        if ext.target_type is not None:
            self._collect_from_type(ext.target_type)

        if ext.ret is not None:
            self._collect_from_type(ext.ret)

        for param in ext.params:
            self._collect_from_param(param)

        self._collect_from_block(ext.body)

    def collect_from_perk_impl(self, perk_impl) -> None:
        """Collect generic instantiations from perk implementation methods."""
        for method in perk_impl.methods:
            self._reset_scope()
            self._bind_self(perk_impl.target_type)

            if method.ret is not None:
                self._collect_from_type(method.ret)

            for param in method.params:
                self._collect_from_param(param)

            self._collect_from_block(method.body)

    def collect_from_const(self, const) -> None:
        """Collect generic instantiations from constant definition."""
        if const.ty is not None:
            self._collect_from_type(const.ty)

    def collect_from_struct(self, struct) -> None:
        """Collect generic instantiations from struct field types."""
        for field in struct.fields:
            if field.ty is not None:
                self._collect_from_type(field.ty)

    def collect_from_enum(self, enum) -> None:
        """Collect generic instantiations from enum variant associated types."""
        for variant in enum.variants:
            for assoc_type in variant.associated_types:
                self._collect_from_type(assoc_type)

    def _collect_from_param(self, param) -> None:
        """Collect generic instantiations from parameter type."""
        if param.ty is not None:
            self._collect_from_type(param.ty)
            if param.name is not None:
                self.variable_types[param.name] = self._resolve_local_type(param.ty)

    def _collect_from_block(self, block) -> None:
        """Collect generic instantiations from block statements."""
        for stmt in block.statements:
            self._collect_from_statement(stmt)

    def _collect_from_statement(self, stmt) -> None:
        """Collect generic instantiations from a statement."""
        from sushi_lang.semantics.ast import Let, Foreach, If, While, Match, Return, ExprStmt, Print, PrintLn, Rebind, Break, Continue

        if isinstance(stmt, Let):
            if stmt.ty is not None:
                self._collect_from_type(stmt.ty)
                # Track variable type for later reference. Resolve a bare struct/enum
                # name (UnknownType) to its concrete type so the shared inferrer can
                # reach the fields and methods of a local when it appears as a generic
                # call argument (#191: identity(p.x), identity(p.method())).
                if stmt.name is not None:
                    self.variable_types[stmt.name] = self._resolve_local_type(stmt.ty)
            if stmt.value is not None:
                self.expression_scanner.scan_expression(stmt.value)
                from sushi_lang.semantics.ast import Name
                if isinstance(stmt.value, Name) and stmt.ty is not None:
                    self.expression_scanner.scan_generic_fn_reference(stmt.value.id, stmt.ty)

        elif isinstance(stmt, Foreach):
            if stmt.item_type is not None:
                self._collect_from_type(stmt.item_type)
            if stmt.iterable is not None:
                self.expression_scanner.scan_expression(stmt.iterable)
            self._collect_from_block(stmt.body)

        elif isinstance(stmt, If):
            for cond, block in stmt.arms:
                self.expression_scanner.scan_expression(cond)
                self._collect_from_block(block)
            if stmt.else_block is not None:
                self._collect_from_block(stmt.else_block)

        elif isinstance(stmt, While):
            if stmt.cond is not None:
                self.expression_scanner.scan_expression(stmt.cond)
            self._collect_from_block(stmt.body)

        elif isinstance(stmt, Match):
            if stmt.scrutinee is not None:
                self.expression_scanner.scan_expression(stmt.scrutinee)
            # A pattern binding is a LOCAL, and a generic called with one needs its type
            # exactly as a `let` local's is needed. `resolved_scrutinee_type` is stamped by
            # the typecheck pass, which runs after this one, so the scrutinee is typed here
            # (#539).
            scrutinee_type = self._infer_scrutinee_type(stmt.scrutinee)
            for arm in stmt.arms:
                from sushi_lang.semantics.ast import Block
                bound = self._bind_pattern_payloads(arm.pattern, scrutinee_type)
                if isinstance(arm.body, Block):
                    self._collect_from_block(arm.body)
                else:
                    # An arm body that is an EXPRESSION introduces no type ANNOTATION, which
                    # is why it used to be skipped -- but it may still CALL a generic, and
                    # the call is what needs collecting (#539).
                    self.expression_scanner.scan_expression(arm.body)
                self._unbind(bound)

        elif isinstance(stmt, Return):
            if stmt.value is not None:
                self.expression_scanner.scan_expression(stmt.value)

        elif isinstance(stmt, (ExprStmt, Print, PrintLn)):
            expr = stmt.expr if hasattr(stmt, 'expr') else stmt.value
            if expr is not None:
                self.expression_scanner.scan_expression(expr)

        elif isinstance(stmt, Rebind):
            if stmt.value is not None:
                self.expression_scanner.scan_expression(stmt.value)

        elif isinstance(stmt, (Break, Continue)):
            pass

    def _collect_from_type(self, ty: "Type") -> None:
        """Collect generic instantiations from a type annotation."""
        resolver = self.expression_scanner._resolver

        if isinstance(ty, GenericTypeRef):
            resolved_type_args = resolver.resolve_type_args(ty.type_args)

            if resolver.contains_unresolvable_in_tuple(resolved_type_args):
                return

            self.instantiations.add((ty.base_name, resolved_type_args))

            for arg in resolved_type_args:
                self._collect_from_type(arg)

        from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType
        if isinstance(ty, ArrayType):
            self._collect_from_type(ty.base_type)
        elif isinstance(ty, DynamicArrayType):
            self._collect_from_type(ty.base_type)

        from sushi_lang.semantics.typesys import StructType
        if isinstance(ty, StructType):
            type_key = f"struct:{ty.name}"
            if type_key in self.visited_types:
                return  # Already processed this struct

            self.visited_types.add(type_key)

            for _field_name, field_type in ty.fields:
                self._collect_from_type(field_type)

        from sushi_lang.semantics.typesys import EnumType
        if isinstance(ty, EnumType):
            type_key = f"enum:{ty.name}"
            if type_key in self.visited_types:
                return  # Already processed this enum

            self.visited_types.add(type_key)

            for variant in ty.variants:
                for assoc_type in variant.associated_types:
                    self._collect_from_type(assoc_type)

            self.visited_types.discard(type_key)  # Allow revisiting from different paths
