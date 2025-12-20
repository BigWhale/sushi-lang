"""Generic type provider system.

This package provides the plugin interface for generic type implementations,
allowing stdlib and user libraries to define generic types through a unified
abstraction.

Key components:
- GenericTypeProvider: Protocol interface for generic type implementations
- GenericTypeRegistry: Central registry for managing providers
- MethodSpec: Specification for generic type methods

Providers:
- HashMapProvider: HashMap<K, V> hash table implementation (in stdlib.generics.collections.hashmap)
"""

from .interface import GenericTypeProvider, MethodSpec
from .registry import GenericTypeRegistry

# Note: HashMapProvider is imported lazily in register_all_providers()
# to avoid circular imports with stdlib.generics.collections.hashmap

__all__ = [
    'GenericTypeProvider',
    'GenericTypeRegistry',
    'MethodSpec',
]


def register_all_providers() -> None:
    """Register all built-in generic type providers.

    Called during compiler initialization to make all generic types available.
    """
    # Import here to avoid circular import at module level
    from sushi_lang.sushi_stdlib.generics.collections.hashmap import register_hashmap_provider
    register_hashmap_provider()
    # Future: register_list_provider(), register_set_provider(), etc.

