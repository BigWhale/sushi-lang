"""Caching for struct and enum LLVM types."""
from __future__ import annotations

from llvmlite import ir


class TypeCache:
    """Cache for LLVM struct and enum types."""

    def __init__(self):
        """Initialize empty type caches."""
        self._struct_cache: dict[str, ir.LiteralStructType] = {}
        self._enum_cache: dict[str, ir.LiteralStructType] = {}

    def get_struct(self, struct_name: str) -> ir.LiteralStructType | None:
        """Get cached struct type."""
        return self._struct_cache.get(struct_name)

    def cache_struct(self, struct_name: str, llvm_type: ir.LiteralStructType):
        """Cache a struct type."""
        self._struct_cache[struct_name] = llvm_type

    def get_enum(self, enum_name: str) -> ir.LiteralStructType | None:
        """Get cached enum type."""
        return self._enum_cache.get(enum_name)

    def cache_enum(self, enum_name: str, llvm_type: ir.LiteralStructType):
        """Cache an enum type."""
        self._enum_cache[enum_name] = llvm_type

    def clear(self):
        """Clear all caches."""
        self._struct_cache.clear()
        self._enum_cache.clear()
