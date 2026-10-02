"""Expression scanning for instantiation collection."""
from __future__ import annotations
from typing import TYPE_CHECKING, Set, Tuple

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.generics.instantiate.types import TypeInferrer

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.type_resolution import TypeResolver
from sushi_lang.semantics.generics.instantiate.type_collection import (
    collect_type_instantiations,
)


class ExpressionScanner:
    """Scans expressions to collect generic type instantiations."""

    def __init__(
        self,
        type_inferrer: "TypeInferrer",
        instantiations: Set[Tuple[str, Tuple["Type", ...]]],
        function_instantiations: Set[Tuple[str, Tuple["Type", ...]]],
        generic_funcs: dict,
        type_validator=None,
        namespaces=None,
        generic_enums=None,
        sites=None,
        file_of=None,
    ):
        """Initialize expression scanner."""
        self.type_inferrer = type_inferrer
        self.instantiations = instantiations
        self.function_instantiations = function_instantiations
        # The first site naming each function instantiation (#579); see
        # `InstantiationCollector.sites`. A plain dict on the unit-test paths.
        self.sites = sites if sites is not None else {}
        self.file_of = file_of or (lambda: None)
        self.generic_funcs = generic_funcs
        self.type_validator = type_validator
        self.namespaces = namespaces
        # Templates by name, for reading a variant's payload types off a GenericTypeRef
        # whose interned instance does not exist yet (#539).
        self.generic_enums = generic_enums
        # Callback to scan a lambda's block body (a statement Block) with the lambda's
        # declared return. Wired by the InstantiationCollector to its
        # FunctionCollector.collect_from_body; left None on
        # the unit-test paths that drive the scanner directly (a block-body lambda is a
        # `let` RHS, which those paths do not construct).
        self.scan_block = None
        # Callback to collect the instantiations a TYPE names. Wired by the
        # InstantiationCollector to its FunctionCollector._collect_from_type, which adds
        # the written SITE and a program-wide visited set; the scanner's own call is the
        # fallback on the unit-test paths that drive the scanner directly. Both walk the
        # type through the one seam.
        self.collect_type = self._collect_from_type
        self._resolver = TypeResolver(
            type_inferrer.struct_table or {},
            type_inferrer.enum_table or {}
        )

    def scan_expression(self, expr) -> None:
        """Recursively collect generic instantiations from expressions."""
        from sushi_lang.semantics.ast import (
            Call, BinaryOp, UnaryOp, IndexAccess, ArrayLiteral,
            EnumConstructor, CastExpr, InterpolatedString, DotCall, TryExpr,
            IntLit, FloatLit, StringLit, BoolLit, Name, Borrow,
            RangeExpr, Spread, MemberAccess, MethodCall, DynamicArrayFrom,
            DynamicArrayNew, BlankLit, Lambda, Block, TupleLiteral,
        )

        if isinstance(expr, Call):
            self._scan_call(expr)
            for arg in expr.args:
                self.scan_expression(arg)
            self.scan_fn_value_arguments(expr, self._call_param_types)

        elif isinstance(expr, DotCall):
            # Chained method calls (result.method())
            # DotCall can be either a method call or enum constructor
            # We treat it as a potential method call for generic return types
            if not self._scan_namespaced_call(expr):
                self._scan_dot_call(expr)
                self.scan_expression(expr.receiver)
            for arg in expr.args:
                self.scan_expression(arg)
            self._scan_static_call(expr)
            self.scan_fn_value_arguments(expr, self._dot_call_param_types)

        elif isinstance(expr, BinaryOp):
            self.scan_expression(expr.left)
            self.scan_expression(expr.right)

        elif isinstance(expr, UnaryOp):
            self.scan_expression(expr.expr)

        elif isinstance(expr, IndexAccess):
            self.scan_expression(expr.array)
            self.scan_expression(expr.index)

        elif isinstance(expr, ArrayLiteral):
            for element in expr.elements:
                self.scan_expression(element.value)
                if element.count is not None:
                    self.scan_expression(element.count)

        elif isinstance(expr, EnumConstructor):
            for arg in expr.args:
                self.scan_expression(arg)
            self.scan_fn_value_arguments(
                expr, lambda node: self._payload_types(node.enum_name, node.variant_name))

        elif isinstance(expr, CastExpr):
            self.scan_expression(expr.expr)

        elif isinstance(expr, InterpolatedString):
            for part in expr.parts:
                if not isinstance(part, str):  # Skip string literals
                    self.scan_expression(part)

        elif isinstance(expr, TryExpr):
            self.scan_expression(expr.expr)

        elif isinstance(expr, Borrow):
            self.scan_expression(expr.expr)

        elif isinstance(expr, RangeExpr):
            self.scan_expression(expr.start)
            self.scan_expression(expr.end)

        elif isinstance(expr, Spread):
            self.scan_expression(expr.value)

        elif isinstance(expr, MemberAccess):
            self.scan_expression(expr.receiver)

        elif isinstance(expr, MethodCall):
            self.scan_expression(expr.receiver)
            for arg in expr.args:
                self.scan_expression(arg)
            self._scan_static_call(expr)
            self.scan_fn_value_arguments(expr, self._method_param_types)

        elif isinstance(expr, DynamicArrayFrom):
            self.scan_expression(expr.elements)

        elif isinstance(expr, TupleLiteral):
            for item in expr.elements:
                self.scan_expression(item)

        elif isinstance(expr, Lambda):
            # Lambda bodies are still present at the instantiate pass (lambda-lifting is the lift pass), so a
            # generic call inside one must be collected. An expression body scans directly; a
            # block body (only valid as a `let` RHS) is walked through the injected block
            # scanner. Types depending on the lambda's own (possibly bare) params can't be
            # inferred here -- that is the pre-existing bare-param limitation, not a new gap.
            if isinstance(expr.body, Block):
                if self.scan_block is not None:
                    self.scan_block(expr.body, expr.ret)
            else:
                self.scan_expression(expr.body)

        elif isinstance(expr, (IntLit, FloatLit, StringLit, BoolLit, Name,
                               BlankLit, DynamicArrayNew)):
            pass

    def _scan_dot_call(self, call) -> None:
        """Detect built-in method calls (via DotCall) with generic return types."""
        from sushi_lang.semantics.ast import DotCall

        if not isinstance(call, DotCall):
            return

        receiver_type = self.type_inferrer.infer_simple_receiver_type(call.receiver)

        if receiver_type is not None:
            return_type = self.type_inferrer.get_builtin_method_return_type(receiver_type, call.method)

            if return_type is not None and isinstance(return_type, GenericTypeRef):
                self._collect_from_type(return_type)

    def _scan_static_call(self, call) -> None:
        """A `<generic>.static(args)` call names the instantiation its ARGUMENTS solve (#573).

        Through the same solver the typecheck pass resolves with, so what is collected here
        is what that pass looks up. A type parameter the arguments leave unsolved is the
        stamp's, and the declared type at the binding site is collected on its own; a call
        the arguments cannot solve is collected by nothing, and the typecheck pass says so
        (CE2060). The arguments were scanned before this, so a nested generic call has
        already been collected.
        """
        from sushi_lang.semantics.ast import Name
        from sushi_lang.semantics.statics import solve_target_type_args, static_template

        receiver = call.receiver
        validator = self.type_validator
        if validator is None or not isinstance(receiver, Name):
            return
        base = receiver.id
        if base in self.type_inferrer.variable_types:
            return
        if (base not in validator.generic_struct_table.by_name
                and base not in validator.generic_enum_table.by_name):
            return
        template = static_template(validator.generic_extension_table, base, call.method)
        if template is None:
            return

        arg_types = [self._infer_arg_type(arg) for arg in call.args]
        type_args, _unsolved = solve_target_type_args(template, arg_types, None)
        if type_args is None:
            return
        resolved = self._resolver.resolve_type_args(tuple(type_args))
        if self._resolver.contains_unresolvable_in_tuple(resolved):
            return
        self.instantiations.add((base, resolved))
        for arg in resolved:
            self.collect_type(arg)

    def _scan_namespaced_call(self, call) -> bool:
        """`alias.f(args)` where the alias names a unit declaring a generic `f`.

        True when this owned the node: the receiver is a namespace and not an
        expression, so walking it as one would look up a variable that is not there.
        The stand-in `Call` is the same device the typecheck pass uses for this shape.
        """
        from sushi_lang.semantics.ast import Call, Name
        if self.namespaces is None or not isinstance(call.receiver, Name):
            return False
        if call.receiver.id in self.type_inferrer.variable_types:
            return False
        binding = self.namespaces.lookup(call.receiver.id, call.method)
        if binding is None:
            return self.namespaces.is_namespace(call.receiver.id)
        if binding.kind == "generic function":
            self._scan_call(Call(callee=Name(id=call.method, loc=call.loc),
                                 args=call.args, type_args=call.type_args,
                                 type_args_loc=call.type_args_loc, loc=call.loc),
                            generic_func=binding.record)
        for arg in call.args:
            self.scan_expression(arg)
        return True

    def _scan_call(self, call, generic_func=None) -> None:
        """Detect generic function calls and infer type arguments.

        A qualified call resolved its declaration through the alias's provider and
        hands it in; a bare call reads the unit's own view (#495).
        """
        from sushi_lang.semantics.ast import Name

        callee = getattr(call, "callee", None)
        if not isinstance(callee, Name):
            return

        function_name = callee.id

        row = self._stdlib_row(function_name, generic_func)
        if row is not None:
            self._intern_stdlib_result(row)
            return

        resolved = self.resolve_generic_call(call, generic_func)
        if resolved is None:
            # Not a generic call, or its type arguments cannot be inferred here. No
            # diagnostic: the typecheck pass reports the real one (CE2060, CE2062).
            return
        generic_func, type_args = resolved

        # D2: identity is (declaring_unit, name, type_args). The declaring unit
        # comes from the definition the ladder resolved for THIS unit (#495).
        self.function_instantiations.add(
            (getattr(generic_func, "unit_name", None), function_name, type_args))
        self._record_function_site(function_name, type_args, getattr(call, "loc", None))
        self._collect_substituted_signature(generic_func, type_args)

    def _record_function_site(self, name: str, type_args, loc) -> None:
        """The first site naming a function instantiation, for CE4006's caret (#579)."""
        if loc is None:
            return
        from sushi_lang.semantics.generics.extension_targets import instantiation_key
        self.sites.setdefault(("fn", instantiation_key(name, tuple(type_args))),
                              (loc, self.file_of()))

    def _stdlib_row(self, function_name: str, generic_func=None):
        """The stdlib row a bare call reaches in this unit, or None (#798).

        Section 8's ladder, as the typecheck pass walks it: a generic or a concrete
        declaration the unit can see answers first, and a registry module answers only
        when the unit imports it. A reader with no tables sees every module.
        """
        from sushi_lang.semantics.stdlib_registry import signature_tables, stdlib_signature

        if generic_func is not None or function_name in (self.generic_funcs or {}):
            return None
        funcs = getattr(self.type_validator, "func_table", None)
        if funcs is None:
            if function_name in (self.type_inferrer.func_table or {}):
                return None
            return next((table[function_name] for table in signature_tables().values()
                         if function_name in table), None)
        scope = getattr(self.namespaces, "scope", None)
        if funcs.lookup(function_name, getattr(scope, "unit", None), scope) is not None:
            return None
        found = funcs.lookup_stdlib_by_name(function_name, scope)
        return stdlib_signature(found[0], function_name) if found is not None else None

    def _intern_stdlib_result(self, sig) -> None:
        """Intern the Result a stdlib row answers, from the row itself (#550).

        A payload that is itself a generic -- `fd_readln` answers
        `Result@(Maybe@(string), E)` -- is interned too: the Result goes through its own
        seam, but a payload enum has to be asked for here or the match on the answer
        sees an unresolved `Maybe@(string)` (CE2048).
        """
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.typesys import UnknownType

        if sig.ok is None:
            return  # a bare answer: no Result to intern

        payload = sig.ok
        if isinstance(payload, GenericTypeRef):
            inner = self._resolve_payload_arg(payload.type_args)
            if inner is not None:
                self.instantiations.add((payload.base_name, inner))
        elif isinstance(payload, UnknownType):
            named = (self.type_inferrer.struct_table.get(payload.name)
                     or self.type_inferrer.enum_table.get(payload.name))
            if named is None:
                return
            payload = named

        error = self.type_inferrer.enum_table.get(sig.error) if sig.error else None
        if error is not None and not isinstance(payload, GenericTypeRef):
            self.instantiations.add(("Result", (payload, error)))

    @staticmethod
    def _resolve_payload_arg(type_args) -> tuple | None:
        """A generic payload's own arguments, or None when one does not resolve."""
        return tuple(type_args) if type_args else None

    def resolve_generic_call(self, call, generic_func=None):
        """The generic declaration a call names and its type arguments, or None.

        A qualified call hands its declaration in; a bare one reads the unit's view.
        Explicit `@(...)` type args (issue #137) override inference; a wrong arity
        answers None and leaves CE2062 to the typecheck pass.
        """
        from sushi_lang.semantics.ast import Call, Name

        if not isinstance(call, Call) or not isinstance(call.callee, Name):
            return None
        if generic_func is None:
            if self._declares_concrete(call.callee.id):
                return None
            generic_func = (self.generic_funcs or {}).get(call.callee.id)
        if generic_func is None:
            return None

        explicit = call.type_args
        if explicit:
            from sushi_lang.semantics.generics.explicit_type_args import (
                resolve_explicit_type_args,
                check_explicit_type_arg_arity,
            )
            if check_explicit_type_arg_arity(generic_func, len(explicit)) is not None:
                return None
            type_args = resolve_explicit_type_args(
                explicit,
                self.type_inferrer.struct_table,
                self.type_inferrer.enum_table,
            )
        else:
            type_args = self._infer_type_args_from_call(call, generic_func)

        if type_args is None:
            return None
        return generic_func, type_args

    def _declares_concrete(self, name: str) -> bool:
        """The unit declares a concrete function of this name, which wins its bare name.

        Section 8's ladder, as the typecheck pass reads it: the unit's own declaration
        answers before a generic an import brought (#963).
        """
        from sushi_lang.semantics.passes.types.calls.user_defined import own_concrete_function
        funcs = getattr(self.type_validator, "func_table", None)
        unit = getattr(getattr(self.namespaces, "scope", None), "unit", None)
        return own_concrete_function(funcs, name, unit) is not None

    def generic_call_type(self, expr):
        """What a generic call yields, as this pass can know it (#549).

        The shared inferrer answers this for a bare generic call now (#556), so what is
        left here is the call this pass resolves through its OWN table of generic
        declarations.
        """
        from sushi_lang.semantics.generics.types import substituted_call_result

        resolved = self.resolve_generic_call(expr)
        if resolved is None:
            return None
        return substituted_call_result(*resolved)

    def _collect_substituted_signature(self, generic_func, type_args) -> None:
        """The instantiations a generic call's SUBSTITUTED signature names (#549, #555).

        `fn wrap@(T)(nom T v) Box@(T)` called with a string answers `Box@(string)`, and
        the program may name that instantiation nowhere else: a `match` arm binds the
        payload, or the value is passed straight on. The generic-target extension and
        perk-implementation copies are cut from the instantiations collected here, so a
        signature type that never reached this set had no method (CE2008). The walk is
        the one a concrete declaration gets: the return, the Result it answers, the
        parameters. Only the wrapper used to be recorded, through a substitution that
        rewrote a top-level type parameter and left `Box@(T)` untouched.

        The declared channel's Result wrapper is recorded too, which is what the
        monomorphizer interns for the concrete copy; a bare generic has none.
        """
        from sushi_lang.semantics.generics.types import (
            substitute_type_params, substituted_call_result, type_param_substitution)

        substitution = type_param_substitution(generic_func, type_args)
        if substitution is None:
            return

        if generic_func.ret is not None:
            ret = substitute_type_params(generic_func.ret, substitution)
            self.collect_type(ret)
            wrapped = substituted_call_result(generic_func, type_args)
            if wrapped is not ret:
                self.collect_type(wrapped)

        for param in generic_func.params:
            if param.ty is not None and not param.is_pack:
                self.collect_type(substitute_type_params(param.ty, substitution))

    def _infer_arg_type(self, arg_expr):
        """Infer a generic call argument's type through the typecheck pass's real inferrer."""
        from sushi_lang.semantics.generics.pack_inference import infer_call_arg_type
        return infer_call_arg_type(self.type_validator, arg_expr)

    def _infer_type_args_from_call(self, call, generic_func) -> tuple["Type", ...] | None:
        """Infer type arguments for generic function call.

        A generic function value is not typed here: the callee types it, once the other
        arguments solve the callee (#1029).
        """
        from sushi_lang.semantics.generics.pack_inference import infer_flat_type_args

        arg_types: list["Type | None"] = []
        for arg_expr in getattr(call, "args", []) or []:
            if self._names_generic_fn_value(arg_expr):
                arg_types.append(None)
                continue
            arg_type = self._infer_arg_type(arg_expr)
            if arg_type is None:
                return None
            arg_types.append(arg_type)

        return infer_flat_type_args(
            generic_func, arg_types,
            self.type_inferrer.struct_table or {}, self.type_inferrer.enum_table or {})

    def scan_generic_fn_reference(self, value, expected_ty) -> None:
        """Record an instantiation for a generic-fn reference with an expected fn type.

        A bare name reads the unit's own view (T2.3); a name behind an alias reads the
        declaration the alias's provider resolves, as a qualified call does (#1017).
        """
        from sushi_lang.semantics.ast import MemberAccess, Name
        from sushi_lang.semantics.typesys import FunctionType
        if not isinstance(expected_ty, FunctionType):
            return
        if isinstance(value, Name):
            name = value.id
            if not self.generic_funcs or name not in self.generic_funcs:
                return
            if name in self.type_inferrer.variable_types:
                return
            if self._declares_concrete(name):
                return
            self._record_fn_reference(name, self.generic_funcs[name], expected_ty)
        elif isinstance(value, MemberAccess):
            binding = self._namespaced_binding(value.receiver, value.member)
            if binding is not None and binding.kind == "generic function":
                self._record_fn_reference(binding.name, binding.record, expected_ty)

    def scan_fn_value_arguments(self, call, position_types) -> None:
        """Record each generic-fn value among a call's arguments against its position's type.

        An argument, a struct field and an enum payload solve a generic function value
        from the declared type of the position, as a typed `let` does (#1021). The
        position types are read only for a call that holds a candidate, so no other call
        is typed here.
        """
        if not any(self.may_name_generic_fn(arg) for arg in call.args):
            return
        for arg, expected in zip(call.args, position_types(call), strict=False):
            self.scan_generic_fn_reference(arg, expected)

    def may_name_generic_fn(self, value) -> bool:
        """The value is a bare generic-function name, or a name behind an alias."""
        from sushi_lang.semantics.ast import MemberAccess, Name
        locals_ = self.type_inferrer.variable_types
        if isinstance(value, Name):
            return value.id in (self.generic_funcs or {}) and value.id not in locals_
        return (isinstance(value, MemberAccess) and isinstance(value.receiver, Name)
                and value.receiver.id not in locals_)

    def _names_generic_fn_value(self, value) -> bool:
        """The value is a generic function, bare or behind an alias, and no local or
        concrete function takes the name."""
        from sushi_lang.semantics.ast import MemberAccess
        if not self.may_name_generic_fn(value):
            return False
        if isinstance(value, MemberAccess):
            binding = self._namespaced_binding(value.receiver, value.member)
            return binding is not None and binding.kind == "generic function"
        return not self._declares_concrete(value.id)

    def _call_param_types(self, call) -> tuple:
        """The declared types of a direct call's argument positions, or () when unknown.

        A concrete function gives its parameter types, a struct construction its field
        types. A generic callee gives its parameter types with the solved type arguments
        put in (#1029), or nothing when the arguments do not solve it.
        """
        from sushi_lang.semantics.ast import Name
        callee = call.callee
        if not isinstance(callee, Name):
            return ()
        name = callee.id
        if name in (self.generic_funcs or {}) and not self._declares_concrete(name):
            return self._substituted_param_types(call)
        sig = self._visible_function(name)
        if sig is not None:
            return tuple(p.ty for p in sig.params)
        return self._field_types(name, call.field_names)

    def _substituted_param_types(self, call, generic_func=None) -> tuple:
        """A generic callee's fixed parameter types under its solved type arguments."""
        from sushi_lang.semantics.generics.types import (
            substitute_type_params, type_param_substitution)
        resolved = self.resolve_generic_call(call, generic_func)
        if resolved is None:
            return ()
        generic_func, type_args = resolved
        substitution = type_param_substitution(generic_func, type_args)
        if substitution is None:
            return ()
        return tuple(None if p.ty is None or p.is_pack
                     else substitute_type_params(p.ty, substitution)
                     for p in generic_func.params)

    def _dot_call_param_types(self, call) -> tuple:
        """The declared types of `X.Y(args)`'s argument positions, or () when unknown.

        `alias.f(...)` gives the concrete function's parameter types, `alias.S(...)` the
        struct's field types, `Enum.Variant(...)` the variant's payload types,
        `Type.static(...)` the static's parameter types (the type bare or behind an
        alias), and a method call on a value its method's parameter types. `alias.g(...)`
        of a generic `g` gives its substituted parameter types (#1029).
        """
        from sushi_lang.semantics.ast import Call, MemberAccess, Name
        receiver = call.receiver
        type_name = None
        if isinstance(receiver, MemberAccess):
            binding = self._namespaced_binding(receiver.receiver, receiver.member)
            if binding is not None and binding.kind in ("struct", "enum"):
                type_name = binding.name
        elif (isinstance(receiver, Name)
                and receiver.id not in self.type_inferrer.variable_types):
            binding = self._namespaced_binding(receiver, call.method)
            if binding is not None:
                if binding.kind == "function" and binding.record is not None:
                    return tuple(p.ty for p in binding.record.params)
                if binding.kind == "generic function":
                    return self._substituted_param_types(
                        Call(callee=Name(id=call.method, loc=call.loc), args=call.args,
                             type_args=call.type_args, loc=call.loc),
                        binding.record)
                if binding.kind == "struct":
                    return self._field_types(binding.name, None)
                return ()
            type_name = receiver.id
        if type_name is None:
            return self._method_param_types(call)
        return (self._payload_types(type_name, call.method)
                or self._static_param_types(type_name, call.method))

    def _method_param_types(self, call) -> tuple:
        """A concrete method's parameter types on the receiver's type, or () when unknown.

        A perk implementation answers first, then a plain extension, as the method ladder
        reads them. A generic-target method gives nothing: its parameter types name the
        target's type parameters.
        """
        validator = self.type_validator
        if validator is None:
            return ()
        receiver_type = self._infer_arg_type(call.receiver)
        if receiver_type is None:
            return ()
        if call.method == "realise":
            return self._realise_default_type(receiver_type)
        method = validator.perk_impl_table.get_method(receiver_type, call.method)
        if method is None:
            method = validator.extension_table.get_method(receiver_type, call.method)
        if method is None or getattr(method, "is_static", False):
            return ()
        return tuple(p.ty for p in method.params)

    @staticmethod
    def _realise_default_type(receiver_type) -> tuple:
        """The type a `.realise(default)` default expects: the Ok or Some arm of the receiver."""
        from sushi_lang.semantics.typesys import EnumType
        base = getattr(receiver_type, "generic_base", None) or getattr(
            receiver_type, "base_name", None)
        if base not in ("Result", "Maybe"):
            return ()
        if isinstance(receiver_type, EnumType):
            variant = receiver_type.get_variant("Ok" if base == "Result" else "Some")
            return tuple(variant.associated_types[:1]) if variant is not None else ()
        type_args = getattr(receiver_type, "type_args", None)
        return (type_args[0],) if type_args else ()

    def _static_param_types(self, type_name: str, method_name: str) -> tuple:
        """A concrete type's static method parameter types, or () when there is none."""
        validator = self.type_validator
        target = ((self.type_inferrer.struct_table or {}).get(type_name)
                  or (self.type_inferrer.enum_table or {}).get(type_name))
        if validator is None or target is None:
            return ()
        method = validator.extension_table.get_method(target, method_name)
        if method is None or not getattr(method, "is_static", False):
            return ()
        return tuple(p.ty for p in method.params)

    def _visible_function(self, name: str):
        """The concrete function a bare call of `name` reaches in this unit, or None."""
        funcs = getattr(self.type_validator, "func_table", None)
        if funcs is None:
            return (self.type_inferrer.func_table or {}).get(name)
        scope = getattr(self.namespaces, "scope", None)
        return funcs.lookup(name, getattr(scope, "unit", None), scope)

    def _field_types(self, name: str, field_names) -> tuple:
        """A concrete struct's field types in argument order, or () when `name` is none."""
        struct = (self.type_inferrer.struct_table or {}).get(name)
        fields = getattr(struct, "fields", None)
        if fields is None:
            return ()
        if field_names:
            by_name = dict(fields)
            return tuple(by_name.get(field) for field in field_names)
        return tuple(ty for _, ty in fields)

    def _payload_types(self, enum_name: str, variant_name: str) -> tuple:
        """A concrete enum variant's payload types, or () when there is none."""
        enum = (self.type_inferrer.enum_table or {}).get(enum_name)
        variant = enum.get_variant(variant_name) if hasattr(enum, "get_variant") else None
        return tuple(variant.associated_types) if variant is not None else ()

    def _namespaced_binding(self, receiver, member: str):
        """What `<alias>.<member>` names, or None when the receiver is not an alias."""
        from sushi_lang.semantics.ast import Name
        if self.namespaces is None or not isinstance(receiver, Name):
            return None
        if receiver.id in self.type_inferrer.variable_types:
            return None
        return self.namespaces.lookup(receiver.id, member)

    def _record_fn_reference(self, name: str, generic_func, expected_ty) -> None:
        """Solve the type arguments from the expected fn type and record the instance."""
        from sushi_lang.semantics.generics.pack_inference import solve_leading_type_args
        type_args = solve_leading_type_args(
            generic_func, list(expected_ty.param_types),
            self.type_inferrer.struct_table or {}, self.type_inferrer.enum_table or {},
            ret_type=expected_ty.ok_type)
        if type_args is None:
            return

        self.function_instantiations.add(
            (getattr(generic_func, "unit_name", None), name, type_args))
        self._collect_substituted_signature(generic_func, type_args)

    def _collect_from_type(self, ty: "Type") -> None:
        """Collect generic instantiations from a type annotation."""
        collect_type_instantiations(
            ty,
            self._resolver,
            self.instantiations,
            structs=self.type_inferrer.struct_table or {},
            enums=self.type_inferrer.enum_table or {},
        )
