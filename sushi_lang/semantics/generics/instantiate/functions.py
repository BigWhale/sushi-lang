"""Function-level instantiation collection."""
from __future__ import annotations
from typing import TYPE_CHECKING, Set, Tuple

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.generics.instantiate.expressions import ExpressionScanner

from sushi_lang.semantics.generics.types import (
    GenericTypeRef, substitute_type_params, type_param_substitution,
)
from sushi_lang.semantics.generics.instantiate.type_collection import (
    collect_type_instantiations,
)


class FunctionCollector:
    """Collects generic instantiations from function-level constructs."""

    def __init__(
        self,
        expression_scanner: "ExpressionScanner",
        instantiations: Set[Tuple[str, Tuple["Type", ...]]],
        variable_types: dict[str, "Type"],
        visited_types: Set[str],
        sites: dict | None = None,
        file_of=None,
    ):
        """Initialize function collector."""
        self.expression_scanner = expression_scanner
        self.instantiations = instantiations
        self.variable_types = variable_types
        self.visited_types = visited_types
        # The first site naming each type instantiation (#579); see
        # `InstantiationCollector.sites`. A plain dict on the unit-test paths.
        self.sites = sites if sites is not None else {}
        self.file_of = file_of or (lambda: None)
        # The declared return type of the body being walked, which solves a generic-fn
        # value in a `return` (#1021). None where nothing is declared.
        self.return_type: "Type | None" = None

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
        inferred = None
        validator = getattr(self.expression_scanner, "type_validator", None)
        if validator is not None:
            inferred = validator.infer_expression_type(scrutinee)
        if inferred is None:
            # A GENERIC call has no monomorphized copy yet, so the validator cannot type
            # it. The scanner has the type arguments and answers from the substituted
            # signature (#549).
            inferred = self.expression_scanner.generic_call_type(scrutinee)
        return inferred

    def _bind_destructure(self, targets, value_type) -> None:
        """A destructure's binders are locals: a written type is collected, and each
        binder takes its written type or the element type of the value."""
        from sushi_lang.semantics.generics.tuples import is_tuple_type, tuple_elements

        elements: tuple = (tuple_elements(value_type) if is_tuple_type(value_type)
                           else (None,) * len(targets))
        if len(elements) != len(targets):
            elements = (None,) * len(targets)
        for target, element in zip(targets, elements, strict=True):
            if target.nested is not None:
                self._bind_destructure(target.nested, element)
                continue
            if target.ty is not None:
                self._collect_from_type(target.ty, target.type_span)
            if target.name is None:
                continue
            bound = self._resolve_local_type(target.ty) if target.ty is not None else element
            if bound is not None:
                self.variable_types[target.name] = bound

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
            substitution = type_param_substitution(template, scrutinee_type.type_args)
            if substitution is None:
                return ()
            for variant in template.variants:
                if variant.name == variant_name:
                    return tuple(
                        substitute_type_params(payload, substitution)
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
        from sushi_lang.semantics.ast import Pattern, NomBinding, TuplePattern
        from sushi_lang.semantics.generics.tuples import is_tuple_type, tuple_elements

        if scrutinee_type is None:
            return []
        if isinstance(pattern, TuplePattern):
            if not is_tuple_type(scrutinee_type):
                return []
            items, payloads = pattern.elements, list(tuple_elements(scrutinee_type))
        elif isinstance(pattern, Pattern):
            items = pattern.bindings
            payloads = self._variant_payload_types(scrutinee_type, pattern.variant_name)
        else:
            return []
        if not payloads:
            return []

        saved: list[tuple[str, "Type | None"]] = []
        for binding, raw_payload in zip(items, payloads, strict=False):
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
            elif isinstance(binding, (Pattern, TuplePattern)):
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
        self.collect_from_signature(func.ret, getattr(func, "err_type", None), func.params,
                                    ret_span=getattr(func, "ret_span", None))
        self.collect_from_body(func.body, func.ret)

    def collect_from_body(self, block, return_type) -> None:
        """Walk a body whose `return` expects `return_type`, and restore the outer one."""
        outer = self.return_type
        self.return_type = return_type
        try:
            self._collect_from_block(block)
        finally:
            self.return_type = outer

    def collect_from_signature(self, ret, err_type, params, ret_span=None) -> None:
        """The instantiations a signature names: the return, the Result it answers, the parameters.

        ONE walk for a unit's `FuncDef` and a library's `FuncSig` (#543): a binary
        library's `fn make_box(i32 v) Box@(i32)` reaches the consumer as a manifest record
        and no unit walk ever sees it, so `Box<i32>` was never interned and the backend
        answered CE0020. A function with a channel answers `Result<T, E>`, so that wrapper
        is recorded too; a bare one answers its return alone.
        """
        if ret is not None:
            self._collect_from_type(ret, ret_span)
            if err_type is not None and not (
                    isinstance(ret, GenericTypeRef) and ret.base_name == "Result"):
                self._collect_from_type(
                    GenericTypeRef(base_name="Result", type_args=(ret, err_type)))

        for param in params:
            self._collect_from_param(param)

    def collect_from_extension(self, ext) -> None:
        """Collect generic instantiations from extension method signature and body."""
        self._reset_scope()
        self._bind_self(ext.target_type)

        if ext.target_type is not None:
            self._collect_from_type(ext.target_type)

        if ext.ret is not None:
            self._collect_from_type(ext.ret, getattr(ext, "ret_span", None))

        self._collect_from_channel(ext)

        for param in ext.params:
            self._collect_from_param(param)

        self.collect_from_body(ext.body, ext.ret)

    def _collect_from_channel(self, decl) -> None:
        """The instantiation a `| E` channel names.

        A function reaches this through `collect_from_signature`. Every other kind that
        writes a channel had no reader at all, so `| MyErr@(i32)` was never interned and
        the name spelled nothing (#668).
        """
        err_type = getattr(decl, "err_type", None)
        if err_type is not None:
            self._collect_from_type(err_type, getattr(decl, "err_span", None))

    def collect_from_perk(self, perk) -> None:
        """The instantiations a perk CONTRACT names: a return, a channel, a parameter.

        A contract has no body, so no body walk reaches it, and it was in no other walk
        either (#668).
        """
        for signature in perk.methods:
            self._reset_scope()

            if signature.ret is not None:
                self._collect_from_type(signature.ret, getattr(signature, "ret_span", None))

            self._collect_from_channel(signature)

            for param in signature.params:
                self._collect_from_param(param)

    def collect_from_perk_impl(self, perk_impl) -> None:
        """Collect generic instantiations from perk implementation methods."""
        for method in perk_impl.methods:
            self._reset_scope()
            self._bind_self(perk_impl.target_type)

            if method.ret is not None:
                self._collect_from_type(method.ret, getattr(method, "ret_span", None))

            self._collect_from_channel(method)

            for param in method.params:
                self._collect_from_param(param)

            self.collect_from_body(method.body, method.ret)

    def collect_from_const(self, const) -> None:
        """Collect generic instantiations from constant definition."""
        if const.ty is not None:
            self._collect_from_type(const.ty, getattr(const, "type_span", None))

    def collect_from_struct(self, struct) -> None:
        """Collect generic instantiations from struct field types."""
        for field in struct.fields:
            if field.ty is not None:
                self._collect_from_type(field.ty, getattr(field, "loc", None))

    def collect_from_enum(self, enum) -> None:
        """Collect generic instantiations from enum variant associated types."""
        for variant in enum.variants:
            for assoc_type in variant.associated_types:
                self._collect_from_type(assoc_type, getattr(variant, "loc", None))

    def _collect_from_param(self, param) -> None:
        """Collect generic instantiations from parameter type."""
        if param.ty is not None:
            self._collect_from_type(param.ty, getattr(param, "type_span", None))
            if param.name is not None:
                self.variable_types[param.name] = self._resolve_local_type(param.ty)

    def _collect_from_block(self, block) -> None:
        """Collect generic instantiations from block statements."""
        for stmt in block.statements:
            self._collect_from_statement(stmt)

    def _collect_from_statement(self, stmt) -> None:
        """Collect generic instantiations from a statement."""
        from sushi_lang.semantics.ast import Let, Foreach, If, While, Match, Return, ExprStmt, Print, PrintLn, Assert, Rebind, Break, Continue, Name

        if isinstance(stmt, Let):
            if stmt.ty is not None:
                self._collect_from_type(stmt.ty, getattr(stmt, "type_span", None))
                # Track variable type for later reference. Resolve a bare struct/enum
                # name (UnknownType) to its concrete type so the shared inferrer can
                # reach the fields and methods of a local when it appears as a generic
                # call argument (#191: identity(p.x), identity(p.method())).
                if stmt.name is not None:
                    self.variable_types[stmt.name] = self._resolve_local_type(stmt.ty)
            if stmt.value is not None:
                # A destructure states no type for its value.
                self.expression_scanner.scan_expression(stmt.value, stmt.ty is not None)
                if stmt.ty is not None:
                    self._scan_fn_value(stmt.value, stmt.ty)
            if stmt.targets is not None:
                self._bind_destructure(stmt.targets, self._infer_scrutinee_type(stmt.value))

        elif isinstance(stmt, Foreach):
            if stmt.item_type is not None:
                self._collect_from_type(stmt.item_type, getattr(stmt, "item_type_span", None))
            if stmt.iterable is not None:
                self.expression_scanner.scan_expression(stmt.iterable, False)
            self._collect_from_block(stmt.body)

        elif isinstance(stmt, If):
            for cond, block in stmt.arms:
                self.expression_scanner.scan_expression(cond, False)
                self._collect_from_block(block)
            if stmt.else_block is not None:
                self._collect_from_block(stmt.else_block)

        elif isinstance(stmt, While):
            if stmt.cond is not None:
                self.expression_scanner.scan_expression(stmt.cond, False)
            self._collect_from_block(stmt.body)

        elif isinstance(stmt, Match):
            if stmt.scrutinee is not None:
                self.expression_scanner.scan_expression(stmt.scrutinee, False)
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
                    self.expression_scanner.scan_expression(arm.body, False)
                self._unbind(bound)

        elif isinstance(stmt, Return):
            if stmt.value is not None:
                self.expression_scanner.scan_expression(stmt.value)
                self._scan_returned_fn_value(stmt.value)

        elif isinstance(stmt, (ExprStmt, Print, PrintLn)):
            expr = stmt.expr if hasattr(stmt, 'expr') else stmt.value
            if expr is not None:
                self.expression_scanner.scan_expression(expr, False)

        elif isinstance(stmt, Assert):
            self.expression_scanner.scan_expression(stmt.cond, False)
            if stmt.message is not None:
                self.expression_scanner.scan_expression(stmt.message)

        elif isinstance(stmt, Rebind):
            if stmt.value is not None:
                self.expression_scanner.scan_expression(stmt.value)
                if self._holds_fn_candidate(stmt.value):
                    target_type = self.expression_scanner._infer_arg_type(stmt.target)
                    if target_type is None and isinstance(stmt.target, Name):
                        target_type = self.variable_types.get(stmt.target.id)
                    self._scan_fn_value(stmt.value, target_type)

        elif isinstance(stmt, (Break, Continue)):
            pass

    def _scan_returned_fn_value(self, value) -> None:
        """Record a generic-fn value that a `return` hands out, against the declared return.

        `return Result.Ok(gen)` and a bare extension's `return gen` both reach it; the
        declared type is the written return, or the success arm of a written Result.
        """
        from sushi_lang.semantics.ast import DotCall, EnumConstructor, Name
        expected = self.return_type
        if isinstance(expected, GenericTypeRef) and expected.base_name == "Result":
            expected = expected.type_args[0] if expected.type_args else None
        if expected is None:
            return
        if (isinstance(value, DotCall) and isinstance(value.receiver, Name)
                and value.receiver.id == "Result" and value.method == "Ok"
                and len(value.args) == 1):
            value = value.args[0]
        elif (isinstance(value, EnumConstructor) and value.enum_name == "Result"
                and value.variant_name == "Ok" and len(value.args) == 1):
            value = value.args[0]
        self._scan_fn_value(value, expected)

    def _holds_fn_candidate(self, value) -> bool:
        """The value, or a constructor argument inside it, may name a generic function."""
        from sushi_lang.semantics.ast import DotCall, EnumConstructor
        if isinstance(value, (DotCall, EnumConstructor)):
            return any(self._holds_fn_candidate(arg) for arg in value.args)
        return self.expression_scanner.may_name_generic_fn(value)

    def _scan_fn_value(self, value, expected) -> None:
        """Record a generic-fn value that a position of type `expected` holds (#1021).

        The value itself, or a payload of a variant of a GENERIC enum that `expected`
        names (`let Maybe@(fn(i32) -> i32) m = Maybe.Some(gen)`): the declared type
        arguments substitute the payload types, which a concrete enum's constructor
        does not need.
        """
        from sushi_lang.semantics.ast import DotCall, EnumConstructor, Name
        if expected is None:
            return
        base = getattr(expected, "base_name", None) or getattr(expected, "generic_base", None)
        variant = None
        if (isinstance(value, DotCall) and isinstance(value.receiver, Name)
                and value.receiver.id == base):
            variant = value.method
        elif isinstance(value, EnumConstructor) and value.enum_name == base:
            variant = value.variant_name
        if variant is None:
            self.expression_scanner.scan_generic_fn_reference(value, expected)
            return
        payloads = self._variant_payload_types(expected, variant)
        for arg, payload in zip(value.args, payloads, strict=False):
            self._scan_fn_value(arg, self._resolve_local_type(payload))

    def _collect_from_type(self, ty: "Type", site=None) -> None:
        """Collect generic instantiations from a type annotation.

        `site` is the span of the written type, when the caller has one: the first is
        kept per instantiation so a constraint violation can point at it (#579).
        """
        inferrer = self.expression_scanner.type_inferrer
        collect_type_instantiations(
            ty,
            self.expression_scanner._resolver,
            self.instantiations,
            structs=inferrer.struct_table or {},
            enums=inferrer.enum_table or {},
            sites=self.sites,
            site=site,
            file=self.file_of(),
            visited=self.visited_types,
        )
