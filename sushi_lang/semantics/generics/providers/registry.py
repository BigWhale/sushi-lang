"""Registry for generic type providers.

The GenericTypeRegistry manages registration and activation of generic type
providers. Types can be:
- Always-on: Core types like Result, Maybe, Own that are always available
- Conditional: Collection types that require explicit use statements

Usage:
    # Register a provider (typically at module load time)
    GenericTypeRegistry.register(HashMapProvider(), always_on=True)

    # Activate a provider (when use statement is processed)
    GenericTypeRegistry.activate("HashMap")

    # Get a provider for dispatch
    provider = GenericTypeRegistry.get("HashMap")
    if provider:
        result = provider.emit_method(...)
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .interface import GenericTypeProvider


class GenericTypeRegistry:
    """Central registry for generic type providers.

    This class manages the lifecycle of generic type providers:
    1. Registration: Providers are registered at module load time
    2. Activation: Providers are activated when use statements are processed
    3. Lookup: Providers are looked up during type collection and code generation

    The registry uses class-level state to maintain provider information across
    the compilation pipeline. Call deactivate_all() between compilations to
    reset the active set.
    """

    # All registered providers by name
    _providers: dict[str, 'GenericTypeProvider'] = {}

    # Names of always-on types (Result, Maybe, Own, etc.)
    _always_on: set[str] = set()

    # Names of currently activated types (via use statements)
    _active: set[str] = set()

    @classmethod
    def register(cls, provider: 'GenericTypeProvider', always_on: bool = False) -> None:
        """Register a generic type provider.

        Args:
            provider: The provider instance to register
            always_on: If True, type is always available without use statement

        Note:
            Providers are typically registered at module import time via
            module-level code in the provider module.
        """
        cls._providers[provider.name] = provider
        if always_on:
            cls._always_on.add(provider.name)

    @classmethod
    def unregister(cls, name: str) -> None:
        """Unregister a provider by name.

        Primarily used for testing.
        """
        cls._providers.pop(name, None)
        cls._always_on.discard(name)
        cls._active.discard(name)

    @classmethod
    def activate(cls, name: str) -> None:
        """Activate a provider for the current compilation.

        Called when a use statement imports a generic type module.

        Args:
            name: The generic type name (e.g., "HashMap", "Set")
        """
        cls._active.add(name)

    @classmethod
    def deactivate_all(cls) -> None:
        """Reset all activated providers.

        Called at the start of each compilation to ensure clean state.
        Does not affect always-on types.
        """
        cls._active.clear()

    @classmethod
    def get(cls, name: str) -> Optional['GenericTypeProvider']:
        """Get a provider if it's available (always-on or activated).

        This is the primary lookup method during compilation. Returns None
        if the type is registered but not currently available.

        Args:
            name: The generic type name

        Returns:
            The provider if available, None otherwise
        """
        if name in cls._always_on or name in cls._active:
            return cls._providers.get(name)
        return None

    @classmethod
    def get_provider(cls, name: str) -> Optional['GenericTypeProvider']:
        """Get a provider regardless of activation status.

        Used for registration checks and type definition lookups that
        don't depend on use statements.

        Args:
            name: The generic type name

        Returns:
            The provider if registered, None otherwise
        """
        return cls._providers.get(name)

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """Check if a provider is registered (regardless of activation)."""
        return name in cls._providers

    @classmethod
    def is_available(cls, name: str) -> bool:
        """Check if a type is available for use (always-on or activated)."""
        return name in cls._always_on or name in cls._active

    @classmethod
    def is_always_on(cls, name: str) -> bool:
        """Check if a type is always-on."""
        return name in cls._always_on

    @classmethod
    def get_all_registered(cls) -> dict[str, 'GenericTypeProvider']:
        """Get all registered providers.

        Used during type collection to register all known generic types.
        """
        return cls._providers.copy()

    @classmethod
    def get_always_on(cls) -> set[str]:
        """Get names of all always-on types."""
        return cls._always_on.copy()

    @classmethod
    def get_active(cls) -> set[str]:
        """Get names of all currently activated types."""
        return cls._active.copy()

    @classmethod
    def clear_all(cls) -> None:
        """Clear all registered providers and state.

        Primarily used for testing to reset to initial state.
        """
        cls._providers.clear()
        cls._always_on.clear()
        cls._active.clear()
