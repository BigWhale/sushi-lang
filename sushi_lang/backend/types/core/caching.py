"""Caching for struct and enum LLVM types.

This module manages caches for complex types to avoid recreating them
during code generation.
"""
from __future__ import annotations

from llvmlite import ir


class TypeCache:
    """Cache for LLVM struct and enum types."""

    def __init__(self):
        """Initialize empty type caches."""
        self._struct_cache: dict[str, ir.LiteralStructType] = {}
        self._enum_cache: dict[str, ir.LiteralStructType] = {}

    def get_struct(self, struct_name: str) -> ir.LiteralStructType | None:
        """Get cached struct type.

        Args:
            struct_name: Name of the struct type.

        Returns:
            Cached LLVM struct type or None if not cached.
        """
        return self._struct_cache.get(struct_name)

    def cache_struct(self, struct_name: str, llvm_type: ir.LiteralStructType):
        """Cache a struct type.

        Args:
            struct_name: Name of the struct type.
            llvm_type: LLVM struct type to cache.
        """
        self._struct_cache[struct_name] = llvm_type

    def get_enum(self, enum_name: str) -> ir.LiteralStructType | None:
        """Get cached enum type.

        Args:
            enum_name: Name of the enum type.

        Returns:
            Cached LLVM enum type or None if not cached.
        """
        return self._enum_cache.get(enum_name)

    def cache_enum(self, enum_name: str, llvm_type: ir.LiteralStructType):
        """Cache an enum type.

        Args:
            enum_name: Name of the enum type.
            llvm_type: LLVM enum type to cache.
        """
        self._enum_cache[enum_name] = llvm_type

    def clear(self):
        """Clear all caches."""
        self._struct_cache.clear()
        self._enum_cache.clear()
