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
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.passes.types import TypeValidator
    from sushi_lang.semantics.ast import MethodCall


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

from sushi_lang.semantics.typesys import BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType  # noqa: E402


@dataclass
class ArrayMethodInferrer:
    """Type inferrer for array methods."""
    receiver_type: ArrayType | DynamicArrayType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.passes.types.arrays import is_builtin_array_method, get_builtin_array_method_return_type
        from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
        from sushi_lang.semantics.typesys import ReferenceType

        # Handle references to arrays (e.g., &i32[])
        actual_type = self.receiver_type.referenced_type if isinstance(self.receiver_type, ReferenceType) else self.receiver_type

        if is_builtin_array_method(self.method_name):
            # Special handling for .get() which returns Maybe<T>
            if self.method_name == "get":
                element_type = actual_type.base_type
                maybe_type = ensure_maybe_type_in_table(self.validator.enum_table, element_type, struct_table=self.validator.struct_table.by_name)
                return maybe_type

            # u8[].to_string_checked() returns Result<string, StdError>
            if self.method_name == "to_string_checked":
                from sushi_lang.semantics.generics.results import ensure_result_type_in_table
                std_error = self.validator.enum_table.by_name.get("StdError")
                return ensure_result_type_in_table(self.validator.enum_table, BuiltinType.STRING, std_error,
                                       struct_table=self.validator.struct_table.by_name)

            return get_builtin_array_method_return_type(self.method_name, actual_type)
        return None


@dataclass
class StringMethodInferrer:
    """Type inferrer for string methods."""
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method, get_builtin_string_method_return_type
        from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
        if is_builtin_string_method(self.method_name):
            # Special handling for methods returning Maybe<T>
            if self.method_name in ("find", "find_last"):
                # Returns Maybe<i32>
                maybe_i32_type = ensure_maybe_type_in_table(self.validator.enum_table, BuiltinType.I32, struct_table=self.validator.struct_table.by_name)
                return maybe_i32_type
            elif self.method_name == "to_i32":
                # Returns Maybe<i32>
                maybe_i32_type = ensure_maybe_type_in_table(self.validator.enum_table, BuiltinType.I32, struct_table=self.validator.struct_table.by_name)
                return maybe_i32_type
            elif self.method_name == "to_i64":
                # Returns Maybe<i64>
                maybe_i64_type = ensure_maybe_type_in_table(self.validator.enum_table, BuiltinType.I64, struct_table=self.validator.struct_table.by_name)
                return maybe_i64_type
            elif self.method_name == "to_f64":
                # Returns Maybe<f64>
                maybe_f64_type = ensure_maybe_type_in_table(self.validator.enum_table, BuiltinType.F64, struct_table=self.validator.struct_table.by_name)
                return maybe_f64_type
            else:
                return get_builtin_string_method_return_type(self.method_name, BuiltinType.STRING)
        return None


@dataclass
class PrimitiveMethodInferrer:
    """Type inferrer for built-in primitive methods (to_str, hash, to_bits).

    Reads the semantics-side table in `generics/primitives.py`, NOT the builtin-method
    registry. The registry is populated by the backend at import time and the pipeline
    imports codegen lazily, after semantic analysis -- so during Pass 2 it is empty, and
    this inferrer silently returned None for every primitive method call (#239). Every
    other family here already reads a semantics-side table; this one was the exception.

    Covers `string` too, which used to be excluded -- see check_string_methods.
    """
    receiver_type: 'Type'
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.primitives import primitive_method_return_type
        return primitive_method_return_type(self.receiver_type, self.method_name)


@dataclass
class StructEnumBuiltinInferrer:
    """Type inferrer for the auto-derived struct/enum builtins (hash, clone).

    Pass 1.8 auto-derives `.hash()` and `.clone()` for every struct and enum and
    deposits them in the builtin-method registry, but no checker here claimed a plain
    StructType/EnumType -- so `p.hash()` inferred None, validate_assignment_compatibility
    took its `value_type is None: return` early exit, and a wrong annotation reached
    codegen and crashed it with CE0017 rather than reporting CE2002 (#239).

    Same shape as PrimitiveMethodInferrer: read the return type straight off the
    registered BuiltinMethod. It emits NO diagnostics -- infer_expression_type runs many
    times per node, so a diagnostic here would duplicate. Arity is the BuiltinMethod's
    own semantic_validator's job, invoked once from passes/types/calls/methods.py.
    """
    receiver_type: 'Type'
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method
        method = get_builtin_method(self.receiver_type, self.method_name)
        if method is not None:
            return method.return_type
        return None


@dataclass
class StdioMethodInferrer:
    """Type inferrer for stdio methods (stdin, stdout, stderr)."""
    receiver_type: BuiltinType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.sushi_stdlib.src.io.stdio import is_builtin_stdio_method, get_builtin_stdio_method_return_type
        if is_builtin_stdio_method(self.method_name):
            return get_builtin_stdio_method_return_type(self.method_name, self.receiver_type)
        return None


@dataclass
class FileMethodInferrer:
    """Type inferrer for file methods."""
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.sushi_stdlib.src.io.files import is_builtin_file_method, get_builtin_file_method_return_type
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
        from sushi_lang.semantics.generics.results import is_builtin_result_method
        from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
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
                    return ensure_maybe_type_in_table(self.validator.enum_table, err_type, struct_table=self.validator.struct_table.by_name)
        return None


@dataclass
class MaybeMethodInferrer:
    """Type inferrer for Maybe<T> methods."""
    receiver_type: EnumType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.maybe import is_builtin_maybe_method
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
        from sushi_lang.semantics.generics.hashmap import is_builtin_hashmap_method, parse_hashmap_types
        if is_builtin_hashmap_method(self.method_name):
            key_type, value_type = parse_hashmap_types(self.receiver_type, self.validator)
            if key_type is not None and value_type is not None:
                if self.method_name in ("get", "remove"):
                    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
                    return ensure_maybe_type_in_table(self.validator.enum_table, value_type, struct_table=self.validator.struct_table.by_name)
                elif self.method_name in ("contains_key", "is_empty"):
                    return BuiltinType.BOOL
                elif self.method_name in ("len", "tombstone_count"):
                    return BuiltinType.I32
                elif self.method_name in ("new", "insert", "rehash", "debug", "free", "destroy"):
                    return BuiltinType.BLANK
                elif self.method_name == "keys":
                    # Return Iterator<K>
                    from sushi_lang.semantics.typesys import IteratorType
                    return IteratorType(element_type=key_type)
                elif self.method_name == "values":
                    # Return Iterator<V>
                    from sushi_lang.semantics.typesys import IteratorType
                    return IteratorType(element_type=value_type)
                elif self.method_name == "entries":
                    # Return Iterator<Entry<K, V>>
                    from sushi_lang.semantics.typesys import IteratorType
                    from sushi_lang.semantics.generics.hashmap import ensure_entry_type_in_struct_table
                    entry_type = ensure_entry_type_in_struct_table(
                        self.validator.struct_table, key_type, value_type
                    )
                    return IteratorType(element_type=entry_type)
        return None


@dataclass
class ListMethodInferrer:
    """Type inferrer for List<T> methods."""
    receiver_type: StructType
    method_name: str
    validator: 'TypeValidator'
    call: Optional['MethodCall'] = None

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.list import is_builtin_list_method
        from sushi_lang.semantics.generics.list import parse_list_types
        import sushi_lang.internals.errors as er

        if is_builtin_list_method(self.method_name):
            # Validate argument count if we have the call node
            if self.call is not None:
                expected_args = {
                    "new": 0, "len": 0, "capacity": 0, "is_empty": 0,
                    "pop": 0, "clear": 0, "shrink_to_fit": 0, "destroy": 0, "free": 0, "debug": 0, "iter": 0,
                    "clone": 0,
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
                    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
                    return ensure_maybe_type_in_table(self.validator.enum_table, element_type, struct_table=self.validator.struct_table.by_name)
                elif self.method_name == "clone":
                    # `.clone()` is the ONLY escape from CE2411 for a List read, so it must
                    # exist for every List (#242). Returns the receiver's own type.
                    return self.receiver_type
                elif self.method_name in ("len", "capacity"):
                    return BuiltinType.I32
                elif self.method_name == "is_empty":
                    return BuiltinType.BOOL
                elif self.method_name == "insert":
                    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
                    std_error = self.validator.enum_table.by_name.get("StdError")
                    if std_error is None:
                        return None
                    return ensure_result_type_in_table(self.validator.enum_table, BuiltinType.BLANK, std_error,
                                       struct_table=self.validator.struct_table.by_name)
                elif self.method_name == "iter":
                    from sushi_lang.semantics.typesys import IteratorType
                    return IteratorType(element_type=element_type)
                elif self.method_name in ("new", "with_capacity", "push", "clear",
                                         "reserve", "shrink_to_fit", "destroy", "free", "debug"):
                    return BuiltinType.BLANK
        return None


@dataclass
class OwnMethodInferrer:
    """Type inferrer for Own<T> methods called on an Own value (.get(), .destroy()).

    Without this, infer_expression_type(own_val.get()) returned None, so an inline
    `match own_val.get()` on a generic-enum payload never resolved its concrete enum type
    and the backend raised CE0121 (#222). `.alloc()` is a constructor-style call on the
    type name, not on an Own value, so it is typed elsewhere and not handled here.
    """
    receiver_type: StructType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.own import get_own_element_type
        if self.method_name == "get":
            # Own<T>.get() yields the payload T (a non-owning borrow).
            try:
                return get_own_element_type(self.receiver_type)
            except (TypeError, IndexError):
                return None
        if self.method_name == "destroy":
            return BuiltinType.BLANK
        if self.method_name == "clone":
            # A fresh Own@(T) over a copied payload -- the receiver's own type (#242).
            return self.receiver_type
        return None


# Register all built-in type checkers
@METHOD_TYPE_REGISTRY.register_checker
def check_array_methods(receiver_type, method_name, validator):
    from sushi_lang.semantics.typesys import ReferenceType
    # Handle both direct array types and references to arrays
    actual_type = receiver_type.referenced_type if isinstance(receiver_type, ReferenceType) else receiver_type
    if isinstance(actual_type, (ArrayType, DynamicArrayType)):
        return ArrayMethodInferrer(receiver_type, method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_string_methods(receiver_type, method_name, validator):
    # Claim only the names this family actually handles. Matching on the receiver type
    # alone used to claim EVERY method name on a string -- and because infer_method_type
    # is first-match-wins, a claim whose inferrer then returns None ends the chain rather
    # than falling through. That is why `string.to_str()` and `string.hash()` stayed
    # un-inferred even with a warm registry: they are primitive methods, they are not in
    # METHOD_SPECS, and check_primitive_methods never got a turn (#239).
    from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method
    if receiver_type == BuiltinType.STRING and is_builtin_string_method(method_name):
        return StringMethodInferrer(method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_primitive_methods(receiver_type, method_name, validator):
    # Every primitive INCLUDING string: to_str/hash exist on string too, and
    # check_string_methods (registered earlier) now declines the names it cannot type.
    # Claim only a (receiver, method) pair the semantics-side table actually carries, so
    # unrelated names fall through to the extension lookup.
    from sushi_lang.semantics.generics.primitives import has_primitive_method
    if not has_primitive_method(receiver_type, method_name):
        return None
    # Perk implementations win at validation (calls/methods.py resolves perks before
    # primitives) and at codegen (dispatcher step 12 before step 15), so inference must
    # let them win too -- otherwise Pass 2 types the call as the built-in's return type
    # while the backend calls the perk body. Same guard the struct/enum checker carries.
    if validator.perk_impl_table.get_method(receiver_type, method_name) is not None:
        return None
    return PrimitiveMethodInferrer(receiver_type, method_name, validator)


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


# Registration ORDER is load-bearing here, twice over.
#
# It must sit BEFORE the container checkers below: infer_method_type returns as soon as a
# checker yields an inferrer, even if that inferrer then returns None. check_result_methods
# claims ANY method name on a `Result<` receiver, so from a later position this checker
# would be unreachable for Result/Maybe -- and those DO carry an auto-derived clone
# (register_enum_clone_method has no container exclusion).
#
# It must also use check_primitive_methods' guard shape -- claim only a (type, name) that
# is genuinely registered -- so it never swallows List<i32>.get, HashMap<..>.insert, etc.
@METHOD_TYPE_REGISTRY.register_checker
def check_struct_enum_builtin_methods(receiver_type, method_name, validator):
    """Claim the auto-derived struct/enum builtins (hash, clone) -- and nothing else."""
    if not isinstance(receiver_type, (StructType, EnumType)):
        return None

    # Own/List/HashMap are named StructTypes that keep their own method paths. This
    # matters concretely for `hash`: register_all_struct_hashes walks EVERY hashable
    # struct with no container exclusion, so a List<i32> monomorph really does carry a
    # registered hash -- while Pass 2 validation reports CE2008 for it. Without this
    # guard, inference and validation would disagree.
    from sushi_lang.semantics.generics.cloning import CONTAINER_PREFIXES
    if receiver_type.name.startswith(CONTAINER_PREFIXES):
        return None

    # Perk implementations win at codegen (dispatcher step 12, before the auto-derived
    # step 13), so inference must let them win too.
    if validator.perk_impl_table.get_method(receiver_type, method_name) is not None:
        return None

    from sushi_lang.sushi_stdlib.src.common import get_builtin_method
    if get_builtin_method(receiver_type, method_name) is None:
        return None

    return StructEnumBuiltinInferrer(receiver_type, method_name, validator)


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


@METHOD_TYPE_REGISTRY.register_checker
def check_own_methods(receiver_type, method_name, validator):
    if isinstance(receiver_type, StructType) and receiver_type.name.startswith("Own<"):
        return OwnMethodInferrer(receiver_type, method_name, validator)
    return None
