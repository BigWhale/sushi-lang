"""Expression scanning for instantiation collection."""
from __future__ import annotations
from typing import TYPE_CHECKING, Set, Tuple

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.generics.instantiate.types import TypeInferrer

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.type_resolution import TypeResolver


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
    ):
        """Initialize expression scanner."""
        self.type_inferrer = type_inferrer
        self.instantiations = instantiations
        self.function_instantiations = function_instantiations
        self.generic_funcs = generic_funcs
        self.type_validator = type_validator
        self.namespaces = namespaces
        # Templates by name, for reading a variant's payload types off a GenericTypeRef
        # whose interned instance does not exist yet (#539).
        self.generic_enums = generic_enums
        # Callback to scan a lambda's block body (a statement Block). Wired by the
        # InstantiationCollector to its FunctionCollector._collect_from_block; left None on
        # the unit-test paths that drive the scanner directly (a block-body lambda is a
        # `let` RHS, which those paths do not construct).
        self.scan_block = None
        # Callback to collect the instantiations a TYPE names. Wired by the
        # InstantiationCollector to its FunctionCollector._collect_from_type, which
        # walks arrays and named types too; the scanner's own GenericTypeRef-only walk
        # is the fallback on the unit-test paths that drive the scanner directly.
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
            DynamicArrayNew, BlankLit, Lambda,
        )

        if isinstance(expr, Call):
            self._scan_call(expr)
            for arg in expr.args:
                self.scan_expression(arg)

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

        elif isinstance(expr, DynamicArrayFrom):
            self.scan_expression(expr.elements)

        elif isinstance(expr, Lambda):
            # Lambda bodies are still present at the instantiate pass (lambda-lifting is the lift pass), so a
            # generic call inside one must be collected. An expression body scans directly; a
            # block body (only valid as a `let` RHS) is walked through the injected block
            # scanner. Types depending on the lambda's own (possibly bare) params can't be
            # inferred here -- that is the pre-existing bare-param limitation, not a new gap.
            if expr.is_block_body:
                if self.scan_block is not None:
                    self.scan_block(expr.body)
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
        from sushi_lang.semantics.typesys import BuiltinType

        callee = getattr(call, "callee", None)
        if not isinstance(callee, Name):
            return

        function_name = callee.id

        if function_name in {'sleep', 'msleep', 'usleep', 'nanosleep'}:
            std_error = self.type_inferrer.enum_table.get("StdError")
            if std_error:
                self.instantiations.add(("Result", (BuiltinType.I32, std_error)))
            return
        elif function_name in {'now', 'monotonic_ns'}:
            std_error = self.type_inferrer.enum_table.get("StdError")
            if std_error:
                self.instantiations.add(("Result", (BuiltinType.I64, std_error)))
            return
        elif function_name == 'setenv':
            env_error = self.type_inferrer.enum_table.get("EnvError")
            if env_error:
                self.instantiations.add(("Result", (BuiltinType.I32, env_error)))
            return
        elif self._scan_registry_signature(function_name):
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
        self._collect_substituted_signature(generic_func, type_args)

    def _scan_registry_signature(self, function_name: str) -> bool:
        """Intern the Result a registry primitive answers, from its own row (#550).

        `<io/files>` and `<net/socket>` each keep one signature table, so the payload
        and the error enum are read rather than spelled here per function. A payload
        that is itself a generic -- `fd_readln` answers `Result@(Maybe@(string), E)` --
        is interned too: the Result goes through its own seam, but a payload enum has
        to be asked for here or the match on the answer sees an unresolved
        `Maybe@(string)` (CE2048).

        Returns True when the name is a registry primitive, so the caller stops.
        """
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.sushi_stdlib.src.io.files_funcs import FILES_SIGNATURES
        from sushi_lang.sushi_stdlib.src.net.socket_funcs import SOCKET_SIGNATURES

        sig = FILES_SIGNATURES.get(function_name) or SOCKET_SIGNATURES.get(function_name)
        if sig is None:
            return False
        if sig.ok is None:
            return True  # a bare answer: no Result to intern

        payload = sig.ok
        if isinstance(payload, GenericTypeRef):
            inner = self._resolve_payload_arg(payload.type_args)
            if inner is not None:
                self.instantiations.add((payload.base_name, inner))
        elif isinstance(payload, UnknownType):
            named = (self.type_inferrer.struct_table.get(payload.name)
                     or self.type_inferrer.enum_table.get(payload.name))
            if named is None:
                return True
            payload = named

        error = self.type_inferrer.enum_table.get(sig.error) if sig.error else None
        if error is not None and not isinstance(payload, GenericTypeRef):
            self.instantiations.add(("Result", (payload, error)))
        return True

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

        The Result wrapper is recorded with StdError -- the call site types a generic's
        channel that way today (#538) -- and with the declared channel too, which is what
        the monomorphizer interns for the concrete copy.
        """
        from sushi_lang.semantics.generics.types import (
            substitute_type_params, substituted_call_result, type_param_substitution)
        from sushi_lang.semantics.typesys import UnknownType

        substitution = type_param_substitution(generic_func, type_args)
        if substitution is None:
            return

        if generic_func.ret is not None:
            ret = substitute_type_params(generic_func.ret, substitution)
            self.collect_type(ret)
            wrapped = substituted_call_result(generic_func, type_args)
            self.collect_type(wrapped)
            if wrapped is not ret:
                self.collect_type(GenericTypeRef(
                    base_name="Result", type_args=(ret, UnknownType("StdError"))))

        for param in generic_func.params:
            if param.ty is not None and not getattr(param, "is_pack", False):
                self.collect_type(substitute_type_params(param.ty, substitution))

    def _infer_arg_type(self, arg_expr):
        """Infer a generic call argument's type through the typecheck pass's real inferrer."""
        from sushi_lang.semantics.ast import Lambda
        if self.type_validator is None:
            return None
        if isinstance(arg_expr, Lambda):
            from sushi_lang.semantics.passes.types.visitor import infer_lambda_type
            return infer_lambda_type(self.type_validator, arg_expr, stamp=False)
        arg_type = self.type_validator.infer_expression_type(arg_expr)
        if arg_type is None and getattr(arg_expr, "method", None) == "clone":
            # `.clone()` returns its receiver's type BY DEFINITION (it is total over
            # types), but at the instantiate pass the receiver's type is still a GenericTypeRef --
            # the interned instance does not exist until the monomorphize pass -- so the typecheck pass's clone
            # inference (keyed on StructType/EnumType) declines it and `f(p.clone())`
            # was never collected (F8; the pin was CE2061). Fall back to the receiver.
            receiver = getattr(arg_expr, "receiver", None)
            if receiver is not None:
                arg_type = self.type_validator.infer_expression_type(receiver)
        return arg_type

    def _infer_type_args_from_call(self, call, generic_func) -> tuple["Type", ...] | None:
        """Infer type arguments for generic function call."""
        from sushi_lang.semantics.generics.pack_inference import infer_flat_type_args

        call_args = getattr(call, "args", []) or []
        arg_types: list["Type"] = []
        for arg_expr in call_args:
            arg_type = self._infer_arg_type(arg_expr)
            if arg_type is None:
                return None
            arg_types.append(arg_type)

        return infer_flat_type_args(
            generic_func,
            arg_types,
            infer_leading=self._infer_leading_type_args,
        )

    def _infer_leading_type_args(
        self, generic_func, leading_arg_types
    ) -> tuple["Type", ...] | None:
        """Infer the leading (non-pack) type-args from already-inferred arg types."""
        from sushi_lang.semantics.type_resolution import resolve_unknown_type

        type_param_map: dict[str, "Type"] = {}

        func_params = [
            p for p in generic_func.params if not getattr(p, "is_pack", False)
        ]

        if len(leading_arg_types) != len(func_params):
            return None

        for arg_type, param in zip(leading_arg_types, func_params, strict=False):
            if param.ty is None:
                # Parameter has no type annotation - shouldn't happen
                return None

            success = self.type_inferrer.unify_types(param.ty, arg_type, type_param_map)
            if not success:
                return None

        leading_type_params = [
            tp for tp in generic_func.type_params
            if not getattr(tp, "is_pack", False)
        ]

        for tp in leading_type_params:
            tp_name = tp.name if hasattr(tp, 'name') else str(tp)
            if tp_name not in type_param_map:
                return None

        type_args = []
        for tp in leading_type_params:
            tp_name = tp.name if hasattr(tp, 'name') else str(tp)
            inferred_type = type_param_map[tp_name]
            resolved_type = resolve_unknown_type(
                inferred_type,
                self.type_inferrer.struct_table or {},
                self.type_inferrer.enum_table or {}
            )
            type_args.append(resolved_type)

        return tuple(type_args)

    def scan_generic_fn_reference(self, name: str, expected_ty) -> None:
        """Record an instantiation for a bare generic-fn reference (T2.3)."""
        from sushi_lang.semantics.typesys import FunctionType
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        if not isinstance(expected_ty, FunctionType):
            return
        if not self.generic_funcs or name not in self.generic_funcs:
            return
        generic_func = self.generic_funcs[name]
        func_params = [p for p in generic_func.params if not getattr(p, "is_pack", False)]
        if len(func_params) != len(expected_ty.param_types):
            return

        type_param_map: dict[str, "Type"] = {}
        for param, exp_param_ty in zip(func_params, expected_ty.param_types, strict=False):
            if param.ty is None:
                return
            if not self.type_inferrer.unify_types(param.ty, exp_param_ty, type_param_map):
                return
        if generic_func.ret is not None:
            if not self.type_inferrer.unify_types(generic_func.ret, expected_ty.ok_type, type_param_map):
                return

        leading_type_params = [
            tp for tp in generic_func.type_params if not getattr(tp, "is_pack", False)
        ]
        type_args = []
        for tp in leading_type_params:
            tp_name = tp.name if hasattr(tp, 'name') else str(tp)
            if tp_name not in type_param_map:
                return  # a type param not solvable from the expected type
            resolved = resolve_unknown_type(
                type_param_map[tp_name],
                self.type_inferrer.struct_table or {},
                self.type_inferrer.enum_table or {},
            )
            type_args.append(resolved)

        self.function_instantiations.add(
            (getattr(generic_func, "unit_name", None), name, tuple(type_args)))
        self._collect_substituted_signature(generic_func, tuple(type_args))

    def _collect_from_type(self, ty: "Type") -> None:
        """Collect generic instantiations from a type annotation."""
        if isinstance(ty, GenericTypeRef):
            resolved_type_args = self._resolver.resolve_type_args(ty.type_args)

            if self._resolver.contains_unresolvable_in_tuple(resolved_type_args):
                return

            self.instantiations.add((ty.base_name, resolved_type_args))

            for arg in resolved_type_args:
                self._collect_from_type(arg)
