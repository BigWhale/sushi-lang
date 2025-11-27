"""
Method type inference registry for built-in types.

This module provides a pluggable registry for method return type inference.
Each built-in type (arrays, strings, HashMap, List, etc.) can register
its own inference logic instead of hardcoding it in the type visitor.

Usage:
    # In a type module (e.g., backend/generics/list/type_inference.py)
    @METHOD_TYPE_REGISTRY.register_checker
    def check_list_methods(receiver_type, method_name, validator):
        if isinstance(receiver_type, StructType) and receiver_type.name.startswith("List<"):
            return ListMethodTypeInferrer(receiver_type, method_name, validator)
        return None
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Protocol, Callable
from dataclasses import dataclass

if TYPE_CHECKING:
    from semantics.typesys import Type
    from semantics.passes.types import TypeValidator


class MethodTypeInferrer(Protocol):
    """Protocol for method type inference handlers."""

    def infer_return_type(self) -> Optional['Type']:
        """Infer the return type of a method call.

        Returns:
            The inferred return type, or None if inference failed.
        """
        ...


# Type for checker functions that determine if they can handle a receiver type
TypeChecker = Callable[['Type', str, 'TypeValidator'], Optional[MethodTypeInferrer]]


class MethodTypeRegistry:
    """Registry for method type inference handlers.

    This registry allows type-specific modules to register their own
    method type inference logic without modifying the core type visitor.
    """

    def __init__(self):
        self._checkers: list[TypeChecker] = []

    def register_checker(self, checker: TypeChecker) -> TypeChecker:
        """Register a type checker function.

        Args:
            checker: Function that checks if it can handle a receiver type
                    and returns an inferrer if so.

        Returns:
            The checker function (for decorator usage).
        """
        self._checkers.append(checker)
        return checker

    def infer_method_type(
        self,
        receiver_type: 'Type',
        method_name: str,
        validator: 'TypeValidator'
    ) -> Optional['Type']:
        """Infer the return type of a method call.

        Args:
            receiver_type: The type of the receiver object.
            method_name: The name of the method being called.
            validator: The type validator instance.

        Returns:
            The inferred return type, or None if no handler could infer it.
        """
        # Try each registered checker in order
        for checker in self._checkers:
            inferrer = checker(receiver_type, method_name, validator)
            if inferrer is not None:
                return inferrer.infer_return_type()

        return None


# Global registry instance
METHOD_TYPE_REGISTRY = MethodTypeRegistry()


# ==============================================================================
# Built-in Type Inference Handlers
# ==============================================================================
# These should eventually be moved to their respective type modules,
# but for now we keep them here for a smooth migration.

from semantics.typesys import BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType


@dataclass
class ArrayMethodInferrer:
    """Type inferrer for array methods."""
    receiver_type: ArrayType | DynamicArrayType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from semantics.validate_arrays import is_builtin_array_method, get_builtin_array_method_return_type
        from backend.generics.maybe import ensure_maybe_type_in_table
        from semantics.typesys import ReferenceType

        # Handle references to arrays (e.g., &i32[])
        actual_type = self.receiver_type.referenced_type if isinstance(self.receiver_type, ReferenceType) else self.receiver_type

        if is_builtin_array_method(self.method_name):
            # Special handling for .get() which returns Maybe<T>
            if self.method_name == "get":
                element_type = actual_type.base_type
                maybe_type = ensure_maybe_type_in_table(self.validator.enum_table, element_type)
                return maybe_type

            return get_builtin_array_method_return_type(self.method_name, actual_type)
        return None


@dataclass
class StringMethodInferrer:
    """Type inferrer for string methods."""
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from stdlib.src.collections.strings import is_builtin_string_method, get_builtin_string_method_return_type
        from backend.generics.maybe import ensure_maybe_type_in_table
        if is_builtin_string_method(self.method_name):
            # Special handling for methods returning Maybe<T>
            if self.method_name == "find":
                # Returns Maybe<i32>
                maybe_i32_type = ensure_maybe_type_in_table(self.validator.enum_table, BuiltinType.I32)
                return maybe_i32_type
            elif self.method_name == "to_i32":
                # Returns Maybe<i32>
                maybe_i32_type = ensure_maybe_type_in_table(self.validator.enum_table, BuiltinType.I32)
                return maybe_i32_type
            elif self.method_name == "to_i64":
                # Returns Maybe<i64>
                maybe_i64_type = ensure_maybe_type_in_table(self.validator.enum_table, BuiltinType.I64)
                return maybe_i64_type
            elif self.method_name == "to_f64":
                # Returns Maybe<f64>
                maybe_f64_type = ensure_maybe_type_in_table(self.validator.enum_table, BuiltinType.F64)
                return maybe_f64_type
            else:
                return get_builtin_string_method_return_type(self.method_name, BuiltinType.STRING)
        return None


@dataclass
class StdioMethodInferrer:
    """Type inferrer for stdio methods (stdin, stdout, stderr)."""
    receiver_type: BuiltinType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from stdlib.src.io.stdio import is_builtin_stdio_method, get_builtin_stdio_method_return_type
        if is_builtin_stdio_method(self.method_name):
            return get_builtin_stdio_method_return_type(self.method_name, self.receiver_type)
        return None


@dataclass
class FileMethodInferrer:
    """Type inferrer for file methods."""
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from stdlib.src.io.files import is_builtin_file_method, get_builtin_file_method_return_type
        if is_builtin_file_method(self.method_name):
            return get_builtin_file_method_return_type(self.method_name)
        return None


@dataclass
class ResultMethodInferrer:
    """Type inferrer for Result<T, E> methods."""
    receiver_type: EnumType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from backend.generics.results import is_builtin_result_method
        from backend.generics.maybe import ensure_maybe_type_in_table
        if is_builtin_result_method(self.method_name):
            ok_variant = self.receiver_type.get_variant("Ok")
            err_variant = self.receiver_type.get_variant("Err")

            if self.method_name in ("is_ok", "is_err"):
                return BuiltinType.BOOL
            elif self.method_name == "realise":
                if ok_variant and ok_variant.associated_types:
                    return ok_variant.associated_types[0]
            elif self.method_name == "expect":
                if ok_variant and ok_variant.associated_types:
                    return ok_variant.associated_types[0]
            elif self.method_name == "err":
                if err_variant and err_variant.associated_types:
                    err_type = err_variant.associated_types[0]
                    return ensure_maybe_type_in_table(self.validator.enum_table, err_type)
        return None


@dataclass
class MaybeMethodInferrer:
    """Type inferrer for Maybe<T> methods."""
    receiver_type: EnumType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from backend.generics.maybe import is_builtin_maybe_method
        if is_builtin_maybe_method(self.method_name):
            some_variant = self.receiver_type.get_variant("Some")
            if some_variant and some_variant.associated_types:
                t_type = some_variant.associated_types[0]
                if self.method_name in ("is_some", "is_none"):
                    return BuiltinType.BOOL
                elif self.method_name in ("realise", "expect"):
                    return t_type
        return None


@dataclass
class HashMapMethodInferrer:
    """Type inferrer for HashMap<K, V> methods."""
    receiver_type: StructType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from backend.generics.hashmap.validation import is_builtin_hashmap_method, parse_hashmap_types
        if is_builtin_hashmap_method(self.method_name):
            key_type, value_type = parse_hashmap_types(self.receiver_type, self.validator)
            if key_type is not None and value_type is not None:
                if self.method_name in ("get", "remove"):
                    from backend.generics.maybe import ensure_maybe_type_in_table
                    return ensure_maybe_type_in_table(self.validator.enum_table, value_type)
                elif self.method_name in ("contains_key", "is_empty"):
                    return BuiltinType.BOOL
                elif self.method_name in ("len", "tombstone_count"):
                    return BuiltinType.I32
                elif self.method_name in ("new", "insert", "rehash", "debug", "free", "destroy"):
                    return BuiltinType.BLANK
                elif self.method_name == "keys":
                    # Return Iterator<K>
                    from semantics.typesys import IteratorType
                    return IteratorType(element_type=key_type)
                elif self.method_name == "values":
                    # Return Iterator<V>
                    from semantics.typesys import IteratorType
                    return IteratorType(element_type=value_type)
        return None


@dataclass
class ListMethodInferrer:
    """Type inferrer for List<T> methods."""
    receiver_type: StructType
    method_name: str
    validator: 'TypeValidator'
    call: Optional['MethodCall'] = None

    def infer_return_type(self) -> Optional['Type']:
        from backend.generics.list.validation import is_builtin_list_method
        from backend.generics.list import parse_list_types
        import internals.errors as er

        if is_builtin_list_method(self.method_name):
            # Validate argument count if we have the call node
            if self.call is not None:
                expected_args = {
                    "new": 0, "len": 0, "capacity": 0, "is_empty": 0,
                    "pop": 0, "clear": 0, "shrink_to_fit": 0, "destroy": 0, "free": 0, "debug": 0, "iter": 0,
                    "with_capacity": 1, "push": 1, "get": 1, "reserve": 1, "remove": 1,
                    "insert": 2,
                }
                expected = expected_args.get(self.method_name, 0)
                got = len(self.call.args)
                if got != expected:
                    er.emit(self.validator.reporter, er.ERR.CE2053, self.call.loc,
                            method=self.method_name, expected=expected, got=got)

            element_type = parse_list_types(self.receiver_type, self.validator)
            if element_type is not None:
                if self.method_name in ("get", "pop", "remove"):
                    from backend.generics.maybe import ensure_maybe_type_in_table
                    return ensure_maybe_type_in_table(self.validator.enum_table, element_type)
                elif self.method_name in ("len", "capacity"):
                    return BuiltinType.I32
                elif self.method_name == "is_empty":
                    return BuiltinType.BOOL
                elif self.method_name == "insert":
                    from backend.generics.results import ensure_result_type_in_table
                    from semantics.typesys import EnumType
                    std_error = self.validator.enum_table.by_name.get("StdError")
                    if std_error is None:
                        return None
                    return ensure_result_type_in_table(self.validator.enum_table, BuiltinType.BLANK, std_error)
                elif self.method_name == "iter":
                    from semantics.typesys import IteratorType
                    return IteratorType(element_type=element_type)
                elif self.method_name in ("new", "with_capacity", "push", "clear",
                                         "reserve", "shrink_to_fit", "destroy", "free", "debug"):
                    return BuiltinType.BLANK
        return None


# Register all built-in type checkers
@METHOD_TYPE_REGISTRY.register_checker
def check_array_methods(receiver_type, method_name, validator):
    from semantics.typesys import ReferenceType
    # Handle both direct array types and references to arrays
    actual_type = receiver_type.referenced_type if isinstance(receiver_type, ReferenceType) else receiver_type
    if isinstance(actual_type, (ArrayType, DynamicArrayType)):
        return ArrayMethodInferrer(receiver_type, method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_string_methods(receiver_type, method_name, validator):
    if receiver_type == BuiltinType.STRING:
        return StringMethodInferrer(method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_stdio_methods(receiver_type, method_name, validator):
    if receiver_type in [BuiltinType.STDIN, BuiltinType.STDOUT, BuiltinType.STDERR]:
        return StdioMethodInferrer(receiver_type, method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_file_methods(receiver_type, method_name, validator):
    if receiver_type == BuiltinType.FILE:
        return FileMethodInferrer(method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_result_methods(receiver_type, method_name, validator):
    if isinstance(receiver_type, EnumType) and receiver_type.name.startswith("Result<"):
        return ResultMethodInferrer(receiver_type, method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_maybe_methods(receiver_type, method_name, validator):
    if isinstance(receiver_type, EnumType) and receiver_type.name.startswith("Maybe<"):
        return MaybeMethodInferrer(receiver_type, method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_hashmap_methods(receiver_type, method_name, validator):
    if isinstance(receiver_type, StructType) and receiver_type.name.startswith("HashMap<"):
        return HashMapMethodInferrer(receiver_type, method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_list_methods(receiver_type, method_name, validator):
    if isinstance(receiver_type, StructType) and receiver_type.name.startswith("List<"):
        return ListMethodInferrer(receiver_type, method_name, validator)
    return None
