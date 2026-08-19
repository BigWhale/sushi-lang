"""Method type inference registry for built-in types."""
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
        """Infer the return type of a method call."""
        ...


TypeChecker = Callable[['Type', str, 'TypeValidator'], Optional[MethodTypeInferrer]]


class MethodTypeRegistry:
    """Registry for method type inference handlers."""

    def __init__(self):
        self._checkers: list[TypeChecker] = []

    def register_checker(self, checker: TypeChecker) -> TypeChecker:
        """Register a type checker function."""
        self._checkers.append(checker)
        return checker

    def infer_method_type(
        self,
        receiver_type: 'Type',
        method_name: str,
        validator: 'TypeValidator'
    ) -> Optional['Type']:
        """Infer the return type of a method call."""
        for checker in self._checkers:
            inferrer = checker(receiver_type, method_name, validator)
            if inferrer is not None:
                return inferrer.infer_return_type()

        return None


METHOD_TYPE_REGISTRY = MethodTypeRegistry()


# Built-in type inference handlers. Belong in their respective type modules eventually.

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
        from sushi_lang.semantics.typesys import deref_type

        actual_type = deref_type(self.receiver_type)

        if is_builtin_array_method(self.method_name):
            # `.get()` READS and `.pop()` REMOVES, but both answer "there is no such
            # element" the same way, so both are Maybe@(T) -- as `List@(T)` already was
            # for both. A bare `T` had to invent a value for the empty case (#377).
            if self.method_name in ("get", "pop"):
                element_type = actual_type.base_type
                maybe_type = ensure_maybe_type_in_table(self.validator.enum_table, element_type, struct_table=self.validator.struct_table.by_name)
                return maybe_type

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
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if is_builtin_string_method(self.method_name):
            ret = get_builtin_string_method_return_type(self.method_name, BuiltinType.STRING)
            # The family table answers a Maybe as a SPELLING (it has no enum table);
            # interning it is this layer's job.
            if isinstance(ret, GenericTypeRef) and ret.base_name == "Maybe":
                return ensure_maybe_type_in_table(
                    self.validator.enum_table, ret.type_args[0],
                    struct_table=self.validator.struct_table.by_name)
            return ret
        return None


@dataclass
class PrimitiveMethodInferrer:
    """Type inferrer for built-in primitive methods (to_str, hash, to_bits)."""
    receiver_type: 'Type'
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.primitives import primitive_method_return_type
        return primitive_method_return_type(self.receiver_type, self.method_name)


@dataclass
class StructEnumBuiltinInferrer:
    """Type inferrer for the auto-derived struct/enum builtins (hash, clone)."""
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
        from sushi_lang.semantics.generics.maybe import is_builtin_maybe_method, maybe_method_return_type
        if is_builtin_maybe_method(self.method_name):
            some_variant = self.receiver_type.get_variant("Some")
            if some_variant and some_variant.associated_types:
                return maybe_method_return_type(
                    some_variant.associated_types[0], self.method_name)
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
                elif self.method_name == "clone":
                    # `.clone()` is the ONLY escape from CE2411 for a HashMap read, so it must
                    # exist for every HashMap. Returns the receiver's own type.
                    return self.receiver_type
                elif self.method_name in ("contains_key", "is_empty"):
                    return BuiltinType.BOOL
                elif self.method_name in ("len", "tombstone_count"):
                    return BuiltinType.I32
                elif self.method_name in ("new", "insert", "rehash", "debug", "free", "destroy"):
                    return BuiltinType.BLANK
                elif self.method_name == "keys":
                    from sushi_lang.semantics.typesys import IteratorType
                    return IteratorType(element_type=key_type)
                elif self.method_name == "values":
                    from sushi_lang.semantics.typesys import IteratorType
                    return IteratorType(element_type=value_type)
                elif self.method_name == "entries":
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
    """Type inferrer for Own<T> methods called on an Own value (.get(), .destroy())."""
    receiver_type: StructType
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.own import get_own_element_type
        if self.method_name == "get":
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


@dataclass
class FunctionMethodInferrer:
    """Type inferrer for the built-in methods on a function value (.clone())."""
    receiver_type: 'Type'
    method_name: str
    validator: 'TypeValidator'

    def infer_return_type(self) -> Optional['Type']:
        from sushi_lang.semantics.generics.closures import function_method_return_type
        return function_method_return_type(self.method_name, self.receiver_type)


@METHOD_TYPE_REGISTRY.register_checker
def check_array_methods(receiver_type, method_name, validator):
    from sushi_lang.semantics.typesys import deref_type
    actual_type = deref_type(receiver_type)
    if isinstance(actual_type, (ArrayType, DynamicArrayType)):
        return ArrayMethodInferrer(receiver_type, method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_string_methods(receiver_type, method_name, validator):
    # Claim only the NAMES this family handles. `infer_method_type` is first-match-wins,
    # so a claim whose inferrer returns None ends the chain instead of falling through --
    # which is how `string.hash()` stayed un-inferred (#239).
    from sushi_lang.sushi_stdlib.src.collections.strings import is_builtin_string_method
    if receiver_type == BuiltinType.STRING and is_builtin_string_method(method_name):
        return StringMethodInferrer(method_name, validator)
    return None


@METHOD_TYPE_REGISTRY.register_checker
def check_primitive_methods(receiver_type, method_name, validator):
    # Every primitive INCLUDING string. Claim only a (receiver, method) pair the
    # semantics table carries, so unrelated names reach the extension lookup.
    from sushi_lang.semantics.generics.primitives import has_primitive_method
    if not has_primitive_method(receiver_type, method_name):
        return None
    # A perk impl wins at validation and at codegen, so inference must let it win too, or
    # Pass 2 types the call as the built-in's return while the backend calls the perk.
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


# Registration ORDER is load-bearing. BEFORE the container checkers, because
# check_result_methods claims ANY name on a `Result<` receiver and would make this
# unreachable for the auto-derived clone. And it must claim only a genuinely registered
# (type, name), so it never swallows `List<i32>.get`.
@METHOD_TYPE_REGISTRY.register_checker
def check_struct_enum_builtin_methods(receiver_type, method_name, validator):
    """Claim the auto-derived struct/enum builtins (hash, clone) -- and nothing else."""
    if not isinstance(receiver_type, (StructType, EnumType)):
        return None

    # Own/List/HashMap keep their own method paths. A List<i32> monomorph really does
    # carry a registered `hash` while validation reports CE2008 for it, so without this
    # guard inference and validation disagree.
    from sushi_lang.semantics.generics.cloning import CONTAINER_PREFIXES
    if receiver_type.name.startswith(CONTAINER_PREFIXES):
        return None

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


@METHOD_TYPE_REGISTRY.register_checker
def check_function_methods(receiver_type, method_name, validator):
    # Claim only the names this family handles: infer_method_type is first-match-wins, so a
    # claim whose inferrer then answers None ends the chain instead of falling through (the
    # #239 shape, recorded on check_string_methods above).
    from sushi_lang.semantics.generics.closures import is_builtin_function_method
    from sushi_lang.semantics.typesys import FunctionType
    if isinstance(receiver_type, FunctionType) and is_builtin_function_method(method_name):
        return FunctionMethodInferrer(receiver_type, method_name, validator)
    return None
