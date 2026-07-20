"""
Common utilities and infrastructure for built-in extension methods.

This module provides the shared infrastructure that all built-in extension method
implementations use, including method registration, lookup, and common validation patterns.
"""

from typing import Dict, Set, Optional, Callable, Any
from dataclasses import dataclass
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import Type, ArrayType, DynamicArrayType, BuiltinType
import llvmlite.ir as ir


@dataclass
class BuiltinMethod:
    """Metadata for a built-in extension method."""
    name: str
    parameter_types: list[Type]
    return_type: Optional[Type]
    description: str
    semantic_validator: Callable[[MethodCall, Type], None]
    llvm_emitter: Callable[[Any, MethodCall, ir.Value, ir.Type, bool], ir.Value]


class BuiltinMethodRegistry:
    """Central registry for all built-in extension methods."""

    def __init__(self):
        self._methods: Dict[Type, Dict[str, BuiltinMethod]] = {}

    def register_method(self, target_type: Type, method: BuiltinMethod) -> None:
        """Register a built-in method for a specific type."""
        if target_type not in self._methods:
            self._methods[target_type] = {}
        self._methods[target_type][method.name] = method

    def get_method(self, target_type: Type, method_name: str) -> Optional[BuiltinMethod]:
        """Get a built-in method for a type, or None if not found."""
        type_methods = self._methods.get(target_type, {})
        return type_methods.get(method_name)

    def has_method(self, target_type: Type, method_name: str) -> bool:
        """Check if a method exists for a type."""
        return self.get_method(target_type, method_name) is not None

    def get_method_names(self, target_type: Type) -> Set[str]:
        """Get all method names available for a type."""
        type_methods = self._methods.get(target_type, {})
        return set(type_methods.keys())

    def get_all_types(self) -> Set[Type]:
        """Get all types that have built-in methods."""
        return set(self._methods.keys())


# Global registry instance
builtin_registry = BuiltinMethodRegistry()


def register_builtin_method(target_type: Type, method: BuiltinMethod) -> None:
    """Convenience function to register a built-in method."""
    builtin_registry.register_method(target_type, method)


def get_builtin_method(target_type: Type, method_name: str) -> Optional[BuiltinMethod]:
    """Convenience function to get a built-in method."""
    return builtin_registry.get_method(target_type, method_name)


def has_builtin_method(target_type: Type, method_name: str) -> bool:
    """Convenience function to check if a built-in method exists."""
    return builtin_registry.has_method(target_type, method_name)


# Hash emitter factories, keyed by type kind ("struct" / "enum" / "array").
#
# Hashability is a semantic property, so Pass 1.8 decides it and registers the
# hash() BuiltinMethod (semantics/generics/hashing.py). The matching LLVM emitter
# is backend code, and semantics must not import the backend -- so the backend
# types modules deposit their emitter *factories* here at import time and
# semantics looks them up. A factory takes the concrete type and returns the
# emitter closure for it.
#
# The lookup is deferred to emission time (see _lazy_hash_emitter in
# semantics/generics/hashing.py), which is inside codegen and therefore after the
# backend has certainly been imported. That keeps registration independent of
# module import order.
_hash_emitter_factories: Dict[str, Callable[[Type], Callable]] = {}


def register_hash_emitter_factory(kind: str, factory: Callable[[Type], Callable]) -> None:
    """Register the backend factory that builds hash() emitters for a type kind."""
    _hash_emitter_factories[kind] = factory


def get_hash_emitter_factory(kind: str) -> Optional[Callable[[Type], Callable]]:
    """Get the hash() emitter factory for a type kind, or None if unregistered."""
    return _hash_emitter_factories.get(kind)


# Clone emitter factories, keyed by type kind ("struct" / "enum"). Same deferred,
# import-order-independent pattern as the hash factories above (#134): Pass 1.8 registers
# the clone() BuiltinMethod in semantics (semantics/generics/cloning.py), and the backend
# types modules deposit the matching LLVM emitter factory here at import time. The factory
# takes the concrete type and returns its emitter closure (a thin wrapper over the existing
# emit_value_clone); lookup is deferred to emission time by _lazy_clone_emitter.
_clone_emitter_factories: Dict[str, Callable[[Type], Callable]] = {}


def register_clone_emitter_factory(kind: str, factory: Callable[[Type], Callable]) -> None:
    """Register the backend factory that builds clone() emitters for a type kind."""
    _clone_emitter_factories[kind] = factory


def get_clone_emitter_factory(kind: str) -> Optional[Callable[[Type], Callable]]:
    """Get the clone() emitter factory for a type kind, or None if unregistered."""
    return _clone_emitter_factories.get(kind)


# Type matching utilities
def matches_fixed_array_type(target_type: Type) -> bool:
    """Check if type is a fixed array type."""
    return isinstance(target_type, ArrayType)


def matches_dynamic_array_type(target_type: Type) -> bool:
    """Check if type is a dynamic array type."""
    return isinstance(target_type, DynamicArrayType)


def matches_any_array_type(target_type: Type) -> bool:
    """Check if type is any array type (fixed or dynamic)."""
    return isinstance(target_type, (ArrayType, DynamicArrayType))


def matches_string_type(target_type: Type) -> bool:
    """Check if type is string type."""
    return target_type == BuiltinType.STRING


def matches_int_type(target_type: Type) -> bool:
    """Check if type is an integer type (signed or unsigned)."""
    return target_type in {
        BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64
    }


def matches_bool_type(target_type: Type) -> bool:
    """Check if type is bool type."""
    return target_type == BuiltinType.BOOL
