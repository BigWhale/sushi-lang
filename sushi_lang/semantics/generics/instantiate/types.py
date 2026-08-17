"""Type inference for instantiation collection."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type

from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter


class TypeInferrer:
    """Simple type inference for instantiation collection."""

    def __init__(self, variable_types: dict[str, "Type"], struct_table: dict, enum_table: dict,
                 func_table: dict | None = None):
        """Initialize type inferrer."""
        self.variable_types = variable_types
        self.struct_table = struct_table
        self.enum_table = enum_table
        self.func_table = func_table or {}

    def infer_simple_receiver_type(self, receiver) -> "Type | None":
        """Simple type inference for method call receivers."""
        from sushi_lang.semantics.ast import Name, StringLit, IntLit, FloatLit, BoolLit, DotCall

        if isinstance(receiver, StringLit):
            return self._infer_stringlit_type(receiver)
        elif isinstance(receiver, IntLit):
            return self._infer_intlit_type(receiver)
        elif isinstance(receiver, FloatLit):
            return self._infer_floatlit_type(receiver)
        elif isinstance(receiver, BoolLit):
            return self._infer_boollit_type(receiver)
        elif isinstance(receiver, DotCall):
            return self._infer_dotcall_type(receiver)
        elif isinstance(receiver, Name):
            return self._infer_name_type(receiver)
        else:
            return None

    def _infer_stringlit_type(self, expr) -> "Type":
        """Infer type for string literal."""
        return BuiltinType.STRING

    def _infer_intlit_type(self, expr) -> "Type":
        """Infer type for integer literal (for future int extension methods)."""
        return BuiltinType.I32

    def _infer_floatlit_type(self, expr) -> "Type":
        """Infer type for float literal (for future float extension methods)."""
        return BuiltinType.F64

    def _infer_boollit_type(self, expr) -> "Type":
        """Infer type for bool literal (for future bool extension methods)."""
        return BuiltinType.BOOL

    def _infer_dotcall_type(self, expr) -> "Type | None":
        """Infer type for chained method call expressions."""
        inner_receiver_type = self.infer_simple_receiver_type(expr.receiver)
        if inner_receiver_type is not None:
            return self.get_builtin_method_return_type(inner_receiver_type, expr.method)
        return None

    def _infer_name_type(self, expr) -> "Type | None":
        """Infer type for name references (variables, builtins)."""
        if expr.id == "stdin":
            return BuiltinType.STDIN
        elif expr.id == "stdout":
            return BuiltinType.STDOUT
        elif expr.id == "stderr":
            return BuiltinType.STDERR

        if expr.id in self.variable_types:
            return self.variable_types[expr.id]

        # For other variables, we would need full type inference which we don't
        # have access to at this stage. Users can use explicit type annotations
        # for complex cases.
        return None

    def get_builtin_method_return_type(self, receiver_type: "Type", method_name: str) -> "Type | None":
        """Get return type of built-in extension methods."""
        if receiver_type == BuiltinType.STRING:
            if method_name in ("find", "find_last"):
                return GenericTypeRef(base_name="Maybe", type_args=(BuiltinType.I32,))
            elif method_name in ("upper", "lower", "cap", "trim", "tleft", "tright"):
                return BuiltinType.STRING

        if isinstance(receiver_type, GenericTypeRef) and receiver_type.base_name == "Maybe":
            if method_name in ("realise", "expect"):
                if receiver_type.type_args:
                    return receiver_type.type_args[0]
            elif method_name in ("is_some", "is_none"):
                return BuiltinType.BOOL

        # Future extension points:
        # - Array methods: array.get() could return Maybe<T>
        # - HashMap methods: map.get() could return Maybe<V>
        # - Result methods: result.and_then() could take generic closures

        return None

    def unify_types(self, param_type: "Type", arg_type: "Type", type_param_map: dict[str, "Type"]) -> bool:
        """Unify parameter type with argument type (Pass 1.5 instantiation collection)."""
        from sushi_lang.semantics.generics.unify import unify_types
        return unify_types(param_type, arg_type, type_param_map)

    def substitute_type_simple(self, ty: "Type", type_params: tuple, type_args: tuple) -> "Type":
        """Simple type substitution for instantiation detection."""
        from sushi_lang.semantics.typesys import UnknownType

        substitution = {}
        for param, arg in zip(type_params, type_args, strict=False):
            param_name = param.name if hasattr(param, 'name') else str(param)
            substitution[param_name] = arg

        if isinstance(ty, TypeParameter):
            param_name = ty.name
            if param_name in substitution:
                return substitution[param_name]
            return ty

        if isinstance(ty, UnknownType):
            type_name = str(ty)
            if type_name in substitution:
                return substitution[type_name]
            return ty

        return ty
