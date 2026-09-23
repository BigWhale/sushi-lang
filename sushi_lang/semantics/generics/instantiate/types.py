"""Type inference for instantiation collection."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type

from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.semantics.ast import BoolLit, DotCall, FloatLit, IntLit, Name, StringLit
from sushi_lang.semantics.generics.types import GenericTypeRef


_LITERAL_TYPES: dict[type, "Type"] = {
    StringLit: BuiltinType.STRING,
    IntLit: BuiltinType.I32,
    FloatLit: BuiltinType.F64,
    BoolLit: BuiltinType.BOOL,
}


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
        literal_type = _LITERAL_TYPES.get(type(receiver))
        if literal_type is not None:
            return literal_type
        if isinstance(receiver, DotCall):
            return self._infer_dotcall_type(receiver)
        if isinstance(receiver, Name):
            return self._infer_name_type(receiver)
        return None

    def _infer_dotcall_type(self, expr) -> "Type | None":
        """Infer type for chained method call expressions."""
        inner_receiver_type = self.infer_simple_receiver_type(expr.receiver)
        if inner_receiver_type is not None:
            return self.get_builtin_method_return_type(inner_receiver_type, expr.method)
        return None

    def _infer_name_type(self, expr) -> "Type | None":
        """Infer type for name references (variables, builtins)."""
        if expr.id in self.variable_types:
            return self.variable_types[expr.id]

        # For other variables, we would need full type inference which we don't
        # have access to at this stage. Users can use explicit type annotations
        # for complex cases.
        return None

    def get_builtin_method_return_type(self, receiver_type: "Type", method_name: str) -> "Type | None":
        """Return type of a built-in method, read from the owning family's table.

        This used to be a third, independent return-type table (#269): it knew some
        string methods and Maybe, and nothing about to_str, hash or to_bits.
        """
        from sushi_lang.sushi_stdlib.src.collections.strings import (
            get_builtin_string_method_return_type,
            is_builtin_string_method,
        )
        from sushi_lang.semantics.generics.primitives import primitive_method_return_type
        from sushi_lang.semantics.generics.maybe import (
            is_builtin_maybe_method,
            maybe_method_return_type,
        )

        if receiver_type == BuiltinType.STRING and is_builtin_string_method(method_name):
            return get_builtin_string_method_return_type(method_name, receiver_type)

        primitive_ret = primitive_method_return_type(receiver_type, method_name)
        if primitive_ret is not None:
            return primitive_ret

        if (isinstance(receiver_type, GenericTypeRef)
                and receiver_type.base_name == "Maybe"
                and receiver_type.type_args
                and is_builtin_maybe_method(method_name)):
            return maybe_method_return_type(receiver_type.type_args[0], method_name)

        return None

    def unify_types(self, param_type: "Type", arg_type: "Type", type_param_map: dict[str, "Type"]) -> bool:
        """Unify parameter type with argument type (the instantiate pass)."""
        from sushi_lang.semantics.generics.unify import unify_types
        return unify_types(param_type, arg_type, type_param_map)
