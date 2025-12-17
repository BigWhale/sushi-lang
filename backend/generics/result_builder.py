"""
Centralized Result<T, E> construction for LLVM backend.

This module provides a unified API for creating and manipulating Result<T, E>
enum types and values throughout the backend. It consolidates scattered
Result handling logic into a single, well-tested component.

Key responsibilities:
1. Result<T, E> type registration in enum table (with caching)
2. LLVM value construction for Ok and Err variants
3. Type name formatting and normalization

Architecture:
- ResultBuilder instance is created per compilation unit
- Caches Result types to avoid redundant enum table lookups
- Uses enum_utils for low-level LLVM struct manipulation
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Dict, Tuple

import llvmlite.ir as ir

from semantics.typesys import EnumType, EnumVariantInfo, BuiltinType, Type, ResultType
from internals.errors import raise_internal_error

if TYPE_CHECKING:
    from backend.interfaces import CodegenProtocol


class ResultBuilder:
    """Centralized Result<T, E> construction for LLVM backend.

    Provides a unified API for:
    - Ensuring Result<T, E> types exist in the enum table
    - Building Ok and Err variant LLVM values
    - Type resolution and caching

    Attributes:
        enum_table: The enum table for type registration.
        _cache: Cache of (ok_type, err_type) -> EnumType mappings.
    """

    def __init__(self, enum_table):
        """Initialize ResultBuilder with an enum table.

        Args:
            enum_table: The enum table for Result type registration.
        """
        self.enum_table = enum_table
        self._cache: Dict[Tuple[str, str], EnumType] = {}

    def _type_to_str(self, t: Type) -> str:
        """Convert a type to its string representation for Result naming.

        Args:
            t: The type to convert.

        Returns:
            String representation suitable for Result<T, E> naming.
        """
        if isinstance(t, BuiltinType):
            return str(t).lower()
        return str(t)

    def ensure_type(self, ok_type: Type, err_type: Type) -> Optional[EnumType]:
        """Ensure Result<T, E> exists in the enum table.

        Creates the Result enum type if it doesn't exist, registers it,
        and sets up hash methods if the type is hashable.

        Args:
            ok_type: The T type parameter for Result<T, E>.
            err_type: The E type parameter for Result<T, E>.

        Returns:
            The EnumType for Result<T, E>, or None if creation failed.
        """
        ok_str = self._type_to_str(ok_type)
        err_str = self._type_to_str(err_type)
        cache_key = (ok_str, err_str)

        # Check cache first
        if cache_key in self._cache:
            return self._cache[cache_key]

        result_enum_name = f"Result<{ok_str}, {err_str}>"

        # Check if it already exists in enum table
        if result_enum_name in self.enum_table.by_name:
            result_enum = self.enum_table.by_name[result_enum_name]
            self._cache[cache_key] = result_enum
            return result_enum

        # Create the Result<T, E> enum type
        ok_variant = EnumVariantInfo(name="Ok", associated_types=(ok_type,))
        err_variant = EnumVariantInfo(name="Err", associated_types=(err_type,))

        result_enum = EnumType(
            name=result_enum_name,
            variants=(ok_variant, err_variant)
        )

        # Register in enum table
        self.enum_table.by_name[result_enum_name] = result_enum
        self.enum_table.order.append(result_enum_name)

        # Register hash method if hashable (after Pass 1.8)
        from backend.types.enums import can_enum_be_hashed, register_enum_hash_method
        can_hash, _ = can_enum_be_hashed(result_enum)
        if can_hash:
            register_enum_hash_method(result_enum)

        self._cache[cache_key] = result_enum
        return result_enum

    def ensure_type_from_result_type(self, result_type: ResultType) -> Optional[EnumType]:
        """Ensure Result<T, E> from a ResultType semantic type.

        Convenience method for handling ResultType directly.

        Args:
            result_type: The ResultType semantic type.

        Returns:
            The EnumType for Result<T, E>.
        """
        return self.ensure_type(result_type.ok_type, result_type.err_type)

    def build_ok_variant(
        self,
        codegen: 'CodegenProtocol',
        result_type: EnumType,
        value: ir.Value
    ) -> ir.Value:
        """Construct Result.Ok(value) LLVM value.

        Args:
            codegen: The LLVM code generator instance.
            result_type: The Result<T, E> enum type.
            value: The LLVM value to wrap in Ok.

        Returns:
            LLVM value representing Result.Ok(value).
        """
        from backend import enum_utils

        ok_tag = result_type.get_variant_index("Ok")
        if ok_tag is None:
            raise_internal_error("CE0035", variant="Ok", enum=result_type.name)

        enum_llvm_type = codegen.types.ll_type(result_type)

        # Create enum with Ok tag
        enum_value = enum_utils.construct_enum_variant(
            codegen, enum_llvm_type, ok_tag,
            data=None, name_prefix=f"{result_type.name}_Ok"
        )

        # Pack value into data field
        if value is not None:
            data_array_type = enum_llvm_type.elements[1]
            temp_alloca = codegen.builder.alloca(data_array_type, name="ok_data_temp")
            data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="ok_data_ptr")

            value_ptr_typed = codegen.builder.bitcast(
                data_ptr, ir.PointerType(value.type), name="ok_ptr_typed"
            )
            codegen.builder.store(value, value_ptr_typed)

            packed_data = codegen.builder.load(temp_alloca, name="packed_ok_data")
            enum_value = enum_utils.set_enum_data(
                codegen, enum_value, packed_data,
                name=f"{result_type.name}_Ok_data"
            )

        return enum_value

    def build_err_variant(
        self,
        codegen: 'CodegenProtocol',
        result_type: EnumType,
        error_value: Optional[ir.Value] = None
    ) -> ir.Value:
        """Construct Result.Err(error) LLVM value.

        Args:
            codegen: The LLVM code generator instance.
            result_type: The Result<T, E> enum type.
            error_value: The LLVM value for the error (optional).

        Returns:
            LLVM value representing Result.Err(error).
        """
        from backend import enum_utils

        err_tag = result_type.get_variant_index("Err")
        if err_tag is None:
            raise_internal_error("CE0035", variant="Err", enum=result_type.name)

        enum_llvm_type = codegen.types.ll_type(result_type)

        # Create enum with Err tag
        enum_value = enum_utils.construct_enum_variant(
            codegen, enum_llvm_type, err_tag,
            data=None, name_prefix=f"{result_type.name}_Err"
        )

        # Pack error data into data field if provided
        if error_value is not None:
            data_array_type = enum_llvm_type.elements[1]
            temp_alloca = codegen.builder.alloca(data_array_type, name="err_data_temp")
            data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="err_data_ptr")

            error_ptr_typed = codegen.builder.bitcast(
                data_ptr, ir.PointerType(error_value.type), name="err_ptr_typed"
            )
            codegen.builder.store(error_value, error_ptr_typed)

            packed_data = codegen.builder.load(temp_alloca, name="packed_err_data")
            enum_value = enum_utils.set_enum_data(
                codegen, enum_value, packed_data,
                name=f"{result_type.name}_Err_data"
            )

        return enum_value

    def build_err_from_return_type(
        self,
        codegen: 'CodegenProtocol',
        return_type: Type,
        error_value: Optional[ir.Value] = None
    ) -> ir.Value:
        """Construct Err variant from a function return type.

        Handles both ResultType and GenericTypeRef with base_name "Result".

        Args:
            codegen: The LLVM code generator instance.
            return_type: The function return type (ResultType or GenericTypeRef).
            error_value: The LLVM value for the error (optional).

        Returns:
            LLVM value representing Result.Err(error).
        """
        from semantics.generics.types import GenericTypeRef

        # Resolve to EnumType
        if isinstance(return_type, ResultType):
            enum_type = self.ensure_type(return_type.ok_type, return_type.err_type)
        elif isinstance(return_type, GenericTypeRef) and return_type.base_name == "Result":
            if len(return_type.type_args) != 2:
                raise_internal_error("CE0040", variant="Err",
                    type=f"Result must have exactly 2 type parameters, got {len(return_type.type_args)}")
            enum_type = self.ensure_type(return_type.type_args[0], return_type.type_args[1])
        else:
            raise_internal_error("CE0040", variant="Err",
                type=f"Expected Result<T, E>, got {return_type}")

        if enum_type is None:
            raise_internal_error("CE0091", type=str(return_type))

        return self.build_err_variant(codegen, enum_type, error_value)


# Module-level convenience function for backward compatibility during migration
def ensure_result_type_in_table(enum_table, ok_type: Type, err_type: Type) -> Optional[EnumType]:
    """Ensure Result<T, E> exists in the enum table.

    This is a convenience wrapper around ResultBuilder for call sites
    that don't need the full builder API. Creates a temporary builder
    instance.

    Args:
        enum_table: The enum table for type registration.
        ok_type: The T type parameter for Result<T, E>.
        err_type: The E type parameter for Result<T, E>.

    Returns:
        The EnumType for Result<T, E>, or None if creation failed.
    """
    builder = ResultBuilder(enum_table)
    return builder.ensure_type(ok_type, err_type)
