"""HashMap<K, V> generic type provider and implementation.

This module provides the HashMapProvider for the generic type system and
implements all HashMap methods using inline LLVM IR emission.

HashMap is a hash table with open addressing using linear probing.
Requires `use <collections/hashmap>` to be available.

Type Parameters:
    K: Key type (must support .hash() and equality comparison)
    V: Value type (any type)

Methods:
    Static:
        new() -> HashMap<K, V>

    Instance (read-only):
        len() -> i32
        is_empty() -> bool
        tombstone_count() -> i32
        get(K) -> Maybe<V>
        contains_key(K) -> bool
        keys() -> Iterator<K>
        values() -> Iterator<V>
        debug() -> ~

    Instance (mutating):
        insert(K, V) -> ~
        remove(K) -> Maybe<V>
        rehash() -> ~
        free() -> ~
        destroy() -> ~
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Union, Optional

from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType
from sushi_lang.semantics.generics.types import GenericStructType, TypeParameter
# Import directly from interface module to avoid circular imports
from sushi_lang.semantics.generics.providers.interface import MethodSpec

# Public API - validation
from .validation import (
    is_builtin_hashmap_method,
    validate_hashmap_method_with_validator
)

# Public API - LLVM emission
from .methods import (
    emit_hashmap_new,
    emit_hashmap_len,
    emit_hashmap_is_empty,
    emit_hashmap_tombstone_count,
    emit_hashmap_get,
    emit_hashmap_contains_key,
    emit_hashmap_insert,
    emit_hashmap_remove,
    emit_hashmap_rehash,
    emit_hashmap_free,
    emit_hashmap_destroy,
    emit_hashmap_debug,
    emit_hashmap_keys,
    emit_hashmap_values,
)

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type, StructType
    from sushi_lang.semantics.ast import MethodCall
    import llvmlite.ir as ir


def emit_hashmap_method(
    codegen,
    expr: 'MethodCall',
    receiver_value: Union['ir.Value', None],
    receiver_type: 'StructType',
    to_i1: bool
) -> 'ir.Value':
    """Emit LLVM IR for HashMap<K, V> method calls.

    HashMap<K, V> is a CORE language feature that uses inline emission.
    This function dispatches to specialized emitters based on method name.

    Args:
        codegen: The LLVM code generator.
        expr: The method call expression.
        receiver_value: The LLVM value of the HashMap (None for new()).
        receiver_type: The HashMap<K, V> struct type.
        to_i1: Whether to convert result to i1.

    Returns:
        The result of the HashMap method call.

    Raises:
        ValueError: If method name is not recognized.
    """
    from sushi_lang.internals.errors import raise_internal_error

    method = expr.method

    # Dispatch to method-specific emitters
    if method == "new":
        result = emit_hashmap_new(codegen, receiver_type)
    elif method == "insert":
        result = emit_hashmap_insert(codegen, expr, receiver_value, receiver_type)
    elif method == "get":
        result = emit_hashmap_get(codegen, expr, receiver_value, receiver_type)
    elif method == "contains_key":
        result = emit_hashmap_contains_key(codegen, expr, receiver_value, receiver_type)
    elif method == "remove":
        result = emit_hashmap_remove(codegen, expr, receiver_value, receiver_type)
    elif method == "rehash":
        result = emit_hashmap_rehash(codegen, receiver_value, receiver_type)
    elif method == "free":
        result = emit_hashmap_free(codegen, receiver_value, receiver_type)
    elif method == "destroy":
        result = emit_hashmap_destroy(codegen, receiver_value, receiver_type)
    elif method == "debug":
        result = emit_hashmap_debug(codegen, receiver_value, receiver_type)
    elif method == "len":
        result = emit_hashmap_len(codegen, receiver_value)
    elif method == "is_empty":
        result = emit_hashmap_is_empty(codegen, receiver_value)
    elif method == "tombstone_count":
        result = emit_hashmap_tombstone_count(codegen, receiver_value)
    elif method == "keys":
        result = emit_hashmap_keys(codegen, expr, receiver_value, receiver_type)
    elif method == "values":
        result = emit_hashmap_values(codegen, expr, receiver_value, receiver_type)
    else:
        raise_internal_error("CE0085", method=method)

    # Convert to i1 if needed
    if to_i1 and method in ("is_empty", "contains_key"):
        result = codegen.utils.as_i1(result)

    return result


class HashMapProvider:
    """Provider for HashMap<K, V> generic struct.

    HashMap is a hash table with open addressing using linear probing.
    It supports automatic resize at 0.75 load factor and tombstone-based
    deletion.

    Type Parameters:
        K: Key type (must support .hash() and equality comparison)
        V: Value type (any type)
    """

    @property
    def name(self) -> str:
        return "HashMap"

    @property
    def type_params(self) -> tuple[TypeParameter, ...]:
        return (TypeParameter("K"), TypeParameter("V"))

    def get_type_definition(self) -> GenericStructType:
        """Return the HashMap<K, V> type definition.

        Fields:
            buckets: Entry<K, V>[] (placeholder as i32[])
            size: i32
            capacity: i32
            tombstones: i32
        """
        return GenericStructType(
            name="HashMap",
            type_params=(TypeParameter(name="K"), TypeParameter(name="V")),
            fields=(
                ("buckets", DynamicArrayType(base_type=BuiltinType.I32)),
                ("size", BuiltinType.I32),
                ("capacity", BuiltinType.I32),
                ("tombstones", BuiltinType.I32),
            )
        )

    def get_required_module(self) -> str:
        """Return the required use statement module."""
        return "collections/hashmap"

    def get_method_specs(self) -> dict[str, MethodSpec]:
        """Return method specifications for HashMap<K, V>."""
        return {
            # Static constructor
            'new': MethodSpec('new', [], 'HashMap<K, V>', is_static=True),

            # Read-only instance methods
            'len': MethodSpec('len', [], BuiltinType.I32),
            'is_empty': MethodSpec('is_empty', [], BuiltinType.BOOL),
            'tombstone_count': MethodSpec('tombstone_count', [], BuiltinType.I32),
            'get': MethodSpec('get', [('key', 'K')], 'Maybe<V>'),
            'contains_key': MethodSpec('contains_key', [('key', 'K')], BuiltinType.BOOL),
            'keys': MethodSpec('keys', [], 'Iterator<K>'),
            'values': MethodSpec('values', [], 'Iterator<V>'),
            'debug': MethodSpec('debug', [], BuiltinType.BLANK),

            # Mutating instance methods
            'insert': MethodSpec('insert', [('key', 'K'), ('value', 'V')], BuiltinType.BLANK, is_mutating=True),
            'remove': MethodSpec('remove', [('key', 'K')], 'Maybe<V>', is_mutating=True),
            'rehash': MethodSpec('rehash', [], BuiltinType.BLANK, is_mutating=True),
            'free': MethodSpec('free', [], BuiltinType.BLANK, is_mutating=True),
            'destroy': MethodSpec('destroy', [], BuiltinType.BLANK, is_mutating=True),
        }

    def is_valid_method(self, method: str) -> bool:
        """Check if method is a valid HashMap method."""
        return method in self.get_method_specs()

    def validate_method(
        self,
        method: str,
        args: list,
        type_args: tuple['Type', ...]
    ) -> Optional['Type']:
        """Validate method call and return the return type.

        Delegates to the existing validation in this module.
        """
        spec = self.get_method_specs().get(method)
        if spec is None:
            return None
        return spec.return_type

    def emit_method(
        self,
        codegen,
        expr,
        receiver_value: Union['ir.Value', None],
        receiver_type: 'StructType',
        to_i1: bool
    ) -> 'ir.Value':
        """Emit LLVM IR for HashMap method call.

        Uses the emit_hashmap_method function from this module.
        """
        return emit_hashmap_method(codegen, expr, receiver_value, receiver_type, to_i1)


# Create singleton instance
_hashmap_provider = HashMapProvider()


def get_hashmap_provider() -> HashMapProvider:
    """Get the HashMap provider instance."""
    return _hashmap_provider


def register_hashmap_provider() -> None:
    """Register the HashMap provider with the registry.

    HashMap requires `use <collections/hashmap>` to be available.
    Set always_on=False to enforce this requirement.
    """
    from sushi_lang.semantics.generics.providers.registry import GenericTypeRegistry
    GenericTypeRegistry.register(_hashmap_provider, always_on=False)


__all__ = [
    # Provider
    'HashMapProvider',
    'get_hashmap_provider',
    'register_hashmap_provider',
    # Validation
    'is_builtin_hashmap_method',
    'validate_hashmap_method_with_validator',
    # Emission entry point
    'emit_hashmap_method',
    # Individual method emitters (for advanced use)
    'emit_hashmap_new',
    'emit_hashmap_insert',
    'emit_hashmap_get',
    'emit_hashmap_contains_key',
    'emit_hashmap_remove',
    'emit_hashmap_len',
    'emit_hashmap_is_empty',
    'emit_hashmap_tombstone_count',
    'emit_hashmap_rehash',
    'emit_hashmap_free',
    'emit_hashmap_destroy',
    'emit_hashmap_debug',
    'emit_hashmap_keys',
    'emit_hashmap_values',
]
