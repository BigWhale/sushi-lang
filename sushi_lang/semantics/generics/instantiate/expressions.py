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
    ):
        """Initialize expression scanner."""
        self.type_inferrer = type_inferrer
        self.instantiations = instantiations
        self.function_instantiations = function_instantiations
        self.generic_funcs = generic_funcs
        self.type_validator = type_validator
        self.namespaces = namespaces
        # Callback to scan a lambda's block body (a statement Block). Wired by the
        # InstantiationCollector to its FunctionCollector._collect_from_block; left None on
        # the unit-test paths that drive the scanner directly (a block-body lambda is a
        # `let` RHS, which those paths do not construct).
        self.scan_block = None
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
        elif function_name == 'getenv':
            self.instantiations.add(("Maybe", (BuiltinType.STRING,)))
            return
        elif function_name in {'file_size', 'mtime', 'ctime'}:
            file_error = self.type_inferrer.enum_table.get("FileError")
            if file_error:
                self.instantiations.add(("Result", (BuiltinType.I64, file_error)))
            return
        elif function_name == 'mode':
            file_error = self.type_inferrer.enum_table.get("FileError")
            if file_error:
                self.instantiations.add(("Result", (BuiltinType.I32, file_error)))
            return
        elif function_name == 'is_symlink':
            file_error = self.type_inferrer.enum_table.get("FileError")
            if file_error:
                self.instantiations.add(("Result", (BuiltinType.BOOL, file_error)))
            return
        elif function_name in {'remove', 'rename', 'copy', 'mkdir', 'rmdir'}:
            file_error = self.type_inferrer.enum_table.get("FileError")
            if file_error:
                self.instantiations.add(("Result", (BuiltinType.I32, file_error)))
            return
        elif function_name == 'read_dir':
            from sushi_lang.semantics.typesys import DynamicArrayType
            file_error = self.type_inferrer.enum_table.get("FileError")
            if file_error:
                self.instantiations.add(
                    ("Result", (DynamicArrayType(BuiltinType.STRING), file_error)))
            return
        elif function_name in {'sock_tcp_connect', 'sock_tcp_listen', 'sock_tcp_accept',
                               'sock_send', 'sock_close', 'sock_local_port',
                               'sock_set_recv_timeout', 'sock_set_send_timeout'}:
            net_error = self.type_inferrer.enum_table.get("NetError")
            if net_error:
                self.instantiations.add(("Result", (BuiltinType.I32, net_error)))
            return
        elif function_name == 'sock_recv':
            from sushi_lang.semantics.typesys import DynamicArrayType
            net_error = self.type_inferrer.enum_table.get("NetError")
            if net_error:
                self.instantiations.add(
                    ("Result", (DynamicArrayType(BuiltinType.U8), net_error)))
            return

        if generic_func is None:
            generic_func = (self.generic_funcs or {}).get(function_name)
        if generic_func is None:
            return

        # Explicit `@(...)` type args (issue #137) override inference. If the arity
        # is wrong we collect nothing here and let the typecheck pass report CE2062.
        explicit = call.type_args
        if explicit:
            from sushi_lang.semantics.generics.explicit_type_args import (
                resolve_explicit_type_args,
                check_explicit_type_arg_arity,
            )
            if check_explicit_type_arg_arity(generic_func, len(explicit)) is not None:
                return
            type_args = resolve_explicit_type_args(
                explicit,
                self.type_inferrer.struct_table,
                self.type_inferrer.enum_table,
            )
        else:
            type_args = self._infer_type_args_from_call(call, generic_func)

        if type_args is not None:
            # D2: identity is (declaring_unit, name, type_args). The declaring unit
            # comes from the definition the ladder resolved for THIS unit (#495).
            self.function_instantiations.add(
                (getattr(generic_func, "unit_name", None), function_name, type_args))

            # IMPORTANT: Also detect Result<T, E> instantiation for the return type
            # All Sushi functions implicitly return Result<T, E> where T is the declared return type
            # and E is StdError by default (unless explicitly specified)
            if generic_func.ret is not None:
                ret_type = self.type_inferrer.substitute_type_simple(
                    generic_func.ret, generic_func.type_params, type_args
                )
                if ret_type is not None:
                    std_error = self.type_inferrer.enum_table.get("StdError")
                    if std_error:
                        self.instantiations.add(("Result", (ret_type, std_error)))

            # Note: We don't emit errors here if inference fails
            # Type validation will catch that in the typecheck pass

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

    def _collect_from_type(self, ty: "Type") -> None:
        """Collect generic instantiations from a type annotation."""
        if isinstance(ty, GenericTypeRef):
            resolved_type_args = self._resolver.resolve_type_args(ty.type_args)

            if self._resolver.contains_unresolvable_in_tuple(resolved_type_args):
                return

            self.instantiations.add((ty.base_name, resolved_type_args))

            for arg in resolved_type_args:
                self._collect_from_type(arg)
