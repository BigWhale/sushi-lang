"""GenericTypeProvider protocol and supporting types.

This module defines the plugin interface for generic type implementations.
Providers implement this interface to define:
- Type definitions (GenericStructType or GenericEnumType)
- Method specifications for validation
- LLVM emission for method calls
"""

from __future__ import annotations
from typing import Protocol, TYPE_CHECKING, Union
from dataclasses import dataclass

if TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.generics.types import GenericStructType, GenericEnumType, TypeParameter
    import llvmlite.ir as ir


@dataclass
class MethodSpec:
    """Specification for a generic type method.

    Used for semantic validation of method calls before code generation.

    Attributes:
        name: Method name (e.g., "new", "insert", "get")
        params: List of (param_name, type) tuples. Type can be a TypeParameter
                name like "K" or "V" for generic parameters.
        return_type: Return type of the method. Can reference type parameters.
        is_static: True for static methods (Type.method()), False for instance
                   methods (instance.method()).
        is_mutating: True if method takes &poke self (modifies receiver).
    """
    name: str
    params: list[tuple[str, Union['Type', str]]]
    return_type: Union['Type', str]
    is_static: bool = False
    is_mutating: bool = False


class GenericTypeProvider(Protocol):
    """Plugin interface for generic type implementations.

    Providers implement this interface to define generic types that can be
    registered with the GenericTypeRegistry. The registry makes types available
    during compilation based on use statements.

    Example implementation for a Set<T> type:

        class SetProvider:
            @property
            def name(self) -> str:
                return "Set"

            @property
            def type_params(self) -> tuple[TypeParameter, ...]:
                return (TypeParameter("T"),)

            def get_type_definition(self) -> GenericStructType:
                return GenericStructType(
                    name="Set",
                    type_params=(TypeParameter("T"),),
                    fields=(("inner", ...),)
                )

            # ... implement remaining methods
    """

    @property
    def name(self) -> str:
        """Return the type name (e.g., "HashMap", "List", "Set")."""
        ...

    @property
    def type_params(self) -> tuple['TypeParameter', ...]:
        """Return the type parameters (e.g., (K, V) for HashMap<K, V>)."""
        ...

    def get_type_definition(self) -> Union['GenericStructType', 'GenericEnumType']:
        """Return the semantic type definition.

        This is used during type collection (Pass 0) to register the generic
        type in the appropriate table (generic_structs or generic_enums).
        """
        ...

    def get_required_module(self) -> str:
        """Return the required use statement module path.

        Returns:
            Module path like "collections/hashmap" for HashMap.
            Used for error messages when type is used without import.
        """
        ...

    def get_method_specs(self) -> dict[str, MethodSpec]:
        """Return method specifications for semantic validation.

        Keys are method names, values are MethodSpec instances describing
        the method signature. This is used during type checking (Pass 2)
        to validate method calls.
        """
        ...

    def is_valid_method(self, method: str) -> bool:
        """Check if a method name is valid for this type.

        This is a fast check used during dispatch to determine if this
        provider should handle a method call.
        """
        ...

    def validate_method(
        self,
        method: str,
        args: list,
        type_args: tuple['Type', ...]
    ) -> Union['Type', None]:
        """Validate a method call and return the return type.

        Args:
            method: Method name being called
            args: Arguments passed to the method (AST nodes)
            type_args: Concrete type arguments for this instantiation
                      (e.g., (i32, string) for HashMap<i32, string>)

        Returns:
            The return type of the method call, or None if validation fails.
        """
        ...

    def emit_method(
        self,
        codegen,
        expr,
        receiver_value: Union['ir.Value', None],
        receiver_type,
        to_i1: bool
    ) -> 'ir.Value':
        """Emit LLVM IR for a method call.

        Args:
            codegen: The LLVMCodegen instance
            expr: The MethodCall AST node
            receiver_value: LLVM value of the receiver (None for static methods)
            receiver_type: Semantic type of the receiver
            to_i1: Whether to convert boolean result to i1

        Returns:
            The LLVM IR value representing the method call result.
        """
        ...
