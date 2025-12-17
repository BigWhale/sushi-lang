# semantics/type_resolution.py
"""
Type resolution utilities for UnknownType to StructType/EnumType conversion.

This module centralizes the logic for resolving UnknownType instances to their
concrete types (StructType or EnumType) using struct and enum tables. This pattern
appears in multiple places across the semantic analysis passes and is now unified here.

Key Components:
- TypeResolver: Centralized class for all type resolution operations
- resolve_unknown_type(): Resolve a single UnknownType to StructType/EnumType
- resolve_type_recursively(): Recursively resolve UnknownType in nested types (arrays, etc.)
"""
from __future__ import annotations
from typing import Dict, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from semantics.typesys import Type, StructType, EnumType


class TypeResolver:
    """Centralized type resolution with caching and validation.

    This class consolidates all type resolution logic previously scattered across:
    - semantics/type_resolution.py (resolve_unknown_type, resolve_type_recursively)
    - semantics/passes/types/resolution.py (resolve_return_type_to_result, resolve_variable_type)
    - semantics/passes/types/compatibility.py (resolve_generic_type_ref)
    - semantics/generics/instantiate/expressions.py (_resolve_type_args)
    - semantics/generics/instantiate/functions.py (_resolve_type_args)

    Usage:
        resolver = TypeResolver(struct_table, enum_table)
        resolved = resolver.resolve(some_type)
        resolved_args = resolver.resolve_type_args(type_args_tuple)
    """

    def __init__(
        self,
        struct_table: Dict[str, 'StructType'],
        enum_table: Dict[str, 'EnumType']
    ):
        """Initialize resolver with type tables.

        Args:
            struct_table: Dictionary mapping struct names to StructType instances
            enum_table: Dictionary mapping enum names to EnumType instances
        """
        self.struct_table = struct_table
        self.enum_table = enum_table

    def resolve(self, ty: 'Type') -> 'Type':
        """Single entry point for all type resolution.

        Resolves UnknownType and GenericTypeRef to concrete types.
        Delegates to resolve_unknown_type for the actual resolution.

        Args:
            ty: The type to resolve

        Returns:
            Resolved concrete type or original if no resolution needed
        """
        return resolve_unknown_type(ty, self.struct_table, self.enum_table)

    def resolve_recursively(self, ty: 'Type') -> 'Type':
        """Recursively resolve UnknownType in nested type structures.

        Delegates to resolve_type_recursively for the actual resolution.

        Args:
            ty: The type to resolve (may contain nested UnknownType instances)

        Returns:
            Resolved type with all nested UnknownType instances resolved
        """
        return resolve_type_recursively(ty, self.struct_table, self.enum_table)

    def resolve_type_args(self, type_args: Tuple['Type', ...]) -> Tuple['Type', ...]:
        """Resolve all UnknownType instances in type_args tuple.

        This centralizes the logic previously duplicated in:
        - semantics/generics/instantiate/expressions.py (_resolve_type_args)
        - semantics/generics/instantiate/functions.py (_resolve_type_args)

        Args:
            type_args: Tuple of types to resolve

        Returns:
            Tuple of resolved types
        """
        from semantics.typesys import ArrayType, DynamicArrayType
        from semantics.generics.types import GenericTypeRef

        resolved_args = []
        for arg in type_args:
            resolved_arg = resolve_unknown_type(arg, self.struct_table, self.enum_table)

            # Recursively resolve nested types
            if isinstance(resolved_arg, (ArrayType, DynamicArrayType)):
                resolved_base = resolve_unknown_type(
                    resolved_arg.base_type,
                    self.struct_table,
                    self.enum_table
                )
                if isinstance(resolved_arg, ArrayType):
                    resolved_arg = ArrayType(base_type=resolved_base, size=resolved_arg.size)
                else:
                    resolved_arg = DynamicArrayType(base_type=resolved_base)
            elif isinstance(resolved_arg, GenericTypeRef):
                resolved_nested_args = self.resolve_type_args(resolved_arg.type_args)
                resolved_arg = GenericTypeRef(
                    base_name=resolved_arg.base_name,
                    type_args=resolved_nested_args
                )

            resolved_args.append(resolved_arg)

        return tuple(resolved_args)

    def resolve_generic_type_ref(self, ty: 'Type') -> 'Type':
        """Resolve GenericTypeRef to monomorphized EnumType or StructType.

        This centralizes the logic previously in:
        - semantics/passes/types/compatibility.py (resolve_generic_type_ref)

        Args:
            ty: The type to resolve (may be GenericTypeRef or any other type)

        Returns:
            The resolved EnumType or StructType if ty is a GenericTypeRef
            with a monomorphized version, otherwise returns ty unchanged.
        """
        from semantics.generics.types import GenericTypeRef

        if isinstance(ty, GenericTypeRef):
            # Build type name: Result<i32> -> "Result<i32>", Box<i32> -> "Box<i32>"
            type_args_str = ", ".join(str(arg) for arg in ty.type_args)
            concrete_name = f"{ty.base_name}<{type_args_str}>"

            # Check if it's a monomorphized enum
            if concrete_name in self.enum_table:
                return self.enum_table[concrete_name]

            # Check if it's a monomorphized struct
            if concrete_name in self.struct_table:
                return self.struct_table[concrete_name]

        return ty

    def contains_unresolvable(self, ty: 'Type', visited: Optional[Set[str]] = None) -> bool:
        """Check if a type contains UnknownType that cannot be resolved.

        Delegates to contains_unresolvable_unknown_type for the actual check.

        Args:
            ty: The type to check
            visited: Set of visited type names to prevent infinite recursion

        Returns:
            True if there are unresolvable UnknownType instances
        """
        return contains_unresolvable_unknown_type(
            ty, self.struct_table, self.enum_table, visited
        )

    def contains_unresolvable_in_tuple(self, type_args: Tuple['Type', ...]) -> bool:
        """Check if any type in a tuple contains unresolvable UnknownType.

        This is a convenience wrapper that handles tuple iteration.

        Args:
            type_args: Tuple of types to check

        Returns:
            True if any type contains unresolvable UnknownType
        """
        for arg in type_args:
            if self.contains_unresolvable(arg):
                return True
        return False


def resolve_unknown_type(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType']
) -> 'Type':
    """Resolve UnknownType or GenericTypeRef to StructType or EnumType if possible.

    If the UnknownType name matches a struct in the struct table or an enum
    in the enum table, return the corresponding concrete type.

    If the type is a GenericTypeRef, generate the concrete type name
    (e.g., "Box<i32>") and look it up in the struct/enum tables.

    Args:
        ty: The type to resolve (may be UnknownType, GenericTypeRef, or any other Type)
        struct_table: Dictionary mapping struct names to StructType instances
        enum_table: Dictionary mapping enum names to EnumType instances

    Returns:
        The resolved type (StructType, EnumType, or original type)

    Examples:
        >>> # Assuming Point is a known struct
        >>> unknown = UnknownType(name="Point")
        >>> resolved = resolve_unknown_type(unknown, struct_table, enum_table)
        >>> isinstance(resolved, StructType)  # True

        >>> # Assuming Box<i32> is a monomorphized generic struct
        >>> generic_ref = GenericTypeRef(base_name="Box", type_args=(BuiltinType.I32,))
        >>> resolved = resolve_unknown_type(generic_ref, struct_table, enum_table)
        >>> isinstance(resolved, StructType)  # True
    """
    from semantics.typesys import UnknownType
    from semantics.generics.types import GenericTypeRef

    if isinstance(ty, UnknownType):
        # Check if this unknown type is actually a known struct
        if ty.name in struct_table:
            return struct_table[ty.name]
        # Check if this unknown type is actually a known enum
        if ty.name in enum_table:
            return enum_table[ty.name]

    # Handle GenericTypeRef - resolve to concrete monomorphized type
    elif isinstance(ty, GenericTypeRef):
        # Special handling for Result<T, E> - convert to ResultType
        if ty.base_name == "Result" and len(ty.type_args) == 2:
            from semantics.typesys import ResultType
            # Resolve type arguments first
            ok_type = resolve_type_recursively(ty.type_args[0], struct_table, enum_table)
            err_type = resolve_type_recursively(ty.type_args[1], struct_table, enum_table)
            # Return ResultType directly - no need to create enum here
            # The backend will call ensure_result_type_in_table() when it needs the LLVM type
            return ResultType(ok_type=ok_type, err_type=err_type)

        # Generate the concrete type name (e.g., "Box<i32>")
        type_arg_strs = ", ".join(str(arg) for arg in ty.type_args)
        concrete_name = f"{ty.base_name}<{type_arg_strs}>"

        # Look up the monomorphized concrete type
        if concrete_name in struct_table:
            return struct_table[concrete_name]
        if concrete_name in enum_table:
            return enum_table[concrete_name]

    return ty


def resolve_type_recursively(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType']
) -> 'Type':
    """Recursively resolve UnknownType in nested type structures.

    Handles resolution for:
    - Simple UnknownType (delegates to resolve_unknown_type)
    - ArrayType with UnknownType base_type
    - DynamicArrayType with UnknownType base_type
    - GenericTypeRef with UnknownType in type_args
    - StructType with UnknownType in field types
    - EnumType with UnknownType in variant associated_types

    Args:
        ty: The type to resolve (may contain nested UnknownType instances)
        struct_table: Dictionary mapping struct names to StructType instances
        enum_table: Dictionary mapping enum names to EnumType instances

    Returns:
        The resolved type with all nested UnknownType instances resolved

    Examples:
        >>> # Array of unknown type Point[] -> Point is a struct
        >>> unknown_array = DynamicArrayType(base_type=UnknownType(name="Point"))
        >>> resolved = resolve_type_recursively(unknown_array, struct_table, enum_table)
        >>> isinstance(resolved.base_type, StructType)  # True
    """
    from semantics.typesys import (
        UnknownType, ArrayType, DynamicArrayType, StructType, EnumType
    )
    from semantics.generics.types import GenericTypeRef

    # First, try to resolve the type itself if it's UnknownType
    resolved_ty = resolve_unknown_type(ty, struct_table, enum_table)

    # Handle array types with recursive base type resolution
    if isinstance(resolved_ty, ArrayType):
        resolved_base = resolve_type_recursively(
            resolved_ty.base_type, struct_table, enum_table
        )
        if resolved_base != resolved_ty.base_type:
            return ArrayType(base_type=resolved_base, size=resolved_ty.size)

    elif isinstance(resolved_ty, DynamicArrayType):
        resolved_base = resolve_type_recursively(
            resolved_ty.base_type, struct_table, enum_table
        )
        if resolved_base != resolved_ty.base_type:
            return DynamicArrayType(base_type=resolved_base)

    # Handle generic type references with recursive type argument resolution
    elif isinstance(resolved_ty, GenericTypeRef):
        resolved_args = tuple(
            resolve_type_recursively(arg, struct_table, enum_table)
            for arg in resolved_ty.type_args
        )
        if resolved_args != resolved_ty.type_args:
            return GenericTypeRef(
                base_name=resolved_ty.base_name,
                type_args=resolved_args
            )

    # Handle struct types with recursive field type resolution
    elif isinstance(resolved_ty, StructType):
        resolved_fields = []
        has_changes = False
        for field_name, field_type in resolved_ty.fields:
            resolved_field_type = resolve_type_recursively(
                field_type, struct_table, enum_table
            )
            resolved_fields.append((field_name, resolved_field_type))
            if resolved_field_type != field_type:
                has_changes = True

        if has_changes:
            return StructType(
                name=resolved_ty.name,
                fields=resolved_fields
            )

    # Handle enum types with recursive variant associated type resolution
    elif isinstance(resolved_ty, EnumType):
        # Note: EnumType resolution is more complex and may need
        # to create new EnumVariant instances. For now, we return
        # the type unchanged and let the caller handle this case.
        # Full recursive enum resolution can be added if needed.
        pass

    return resolved_ty


def contains_unresolvable_unknown_type(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType'],
    visited: Optional[Set[str]] = None
) -> bool:
    """Check if a type contains UnknownType that cannot be resolved.

    Recursively checks for UnknownType instances in the type structure
    and attempts to resolve them. Returns True only if there are UnknownType
    instances that cannot be resolved to StructType or EnumType.

    Handles recursive types (e.g., Own<Expr> in enum Expr) by tracking visited types.

    Args:
        ty: The type to check
        struct_table: Dictionary mapping struct names to StructType instances
        enum_table: Dictionary mapping enum names to EnumType instances
        visited: Set of visited type names to prevent infinite recursion

    Returns:
        True if there are unresolvable UnknownType instances, False otherwise

    Examples:
        >>> # MyStruct is known, but OtherType is not
        >>> arr_type = DynamicArrayType(base_type=UnknownType(name="OtherType"))
        >>> contains_unresolvable_unknown_type(arr_type, struct_table, enum_table)
        True
    """
    from semantics.typesys import (
        UnknownType, ArrayType, DynamicArrayType, StructType, EnumType
    )
    from semantics.generics.types import GenericTypeRef

    # Initialize visited set on first call
    if visited is None:
        visited = set()

    # Try to resolve the type
    resolved_ty = resolve_unknown_type(ty, struct_table, enum_table)

    # If still UnknownType after resolution, it's unresolvable
    if isinstance(resolved_ty, UnknownType):
        return True

    # Create a unique key for this type to detect cycles
    type_key = None
    if isinstance(resolved_ty, StructType):
        type_key = f"struct:{resolved_ty.name}"
    elif isinstance(resolved_ty, EnumType):
        type_key = f"enum:{resolved_ty.name}"

    # Check for cycles - if we've seen this type before, assume it's resolvable
    if type_key and type_key in visited:
        return False

    # Mark this type as visited
    if type_key:
        visited = visited | {type_key}

    # Recursively check nested types
    if isinstance(resolved_ty, (ArrayType, DynamicArrayType)):
        return contains_unresolvable_unknown_type(
            resolved_ty.base_type, struct_table, enum_table, visited
        )

    elif isinstance(resolved_ty, GenericTypeRef):
        return any(
            contains_unresolvable_unknown_type(arg, struct_table, enum_table, visited)
            for arg in resolved_ty.type_args
        )

    elif isinstance(resolved_ty, StructType):
        return any(
            contains_unresolvable_unknown_type(field_type, struct_table, enum_table, visited)
            for _, field_type in resolved_ty.fields
        )

    elif isinstance(resolved_ty, EnumType):
        return any(
            contains_unresolvable_unknown_type(assoc_type, struct_table, enum_table, visited)
            for variant in resolved_ty.variants
            for assoc_type in variant.associated_types
        )

    return False


def parse_type_string(
    type_str: str,
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType']
) -> 'Type':
    """Parse a type string from a manifest file back to a Type object.

    Supports:
    - Primitive types: i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool, string
    - Blank type: ~
    - Array types: i32[], string[]
    - Fixed arrays: i32[10]
    - Struct types (looked up in struct_table)
    - Enum types (looked up in enum_table)

    Args:
        type_str: Type string from manifest (e.g., "i32", "Result<bool, StdError>")
        struct_table: Dictionary mapping struct names to StructType instances
        enum_table: Dictionary mapping enum names to EnumType instances

    Returns:
        Parsed Type object
    """
    from semantics.typesys import (
        BuiltinType, ArrayType, DynamicArrayType, UnknownType
    )

    type_str = type_str.strip()

    # Blank type
    if type_str == "~":
        return BuiltinType.BLANK

    # Primitive types
    primitives = {
        "i8": BuiltinType.I8,
        "i16": BuiltinType.I16,
        "i32": BuiltinType.I32,
        "i64": BuiltinType.I64,
        "u8": BuiltinType.U8,
        "u16": BuiltinType.U16,
        "u32": BuiltinType.U32,
        "u64": BuiltinType.U64,
        "f32": BuiltinType.F32,
        "f64": BuiltinType.F64,
        "bool": BuiltinType.BOOL,
        "string": BuiltinType.STRING,
    }
    if type_str in primitives:
        return primitives[type_str]

    # Dynamic arrays: type[]
    if type_str.endswith("[]"):
        base_str = type_str[:-2]
        base_type = parse_type_string(base_str, struct_table, enum_table)
        return DynamicArrayType(base_type=base_type)

    # Fixed arrays: type[size]
    if type_str.endswith("]") and "[" in type_str:
        bracket_idx = type_str.rfind("[")
        size_str = type_str[bracket_idx+1:-1]
        base_str = type_str[:bracket_idx]
        if size_str.isdigit():
            base_type = parse_type_string(base_str, struct_table, enum_table)
            return ArrayType(base_type=base_type, size=int(size_str))

    # Check struct table
    if type_str in struct_table:
        return struct_table[type_str]

    # Check enum table
    if type_str in enum_table:
        return enum_table[type_str]

    # Unknown type - will be resolved later or cause error
    return UnknownType(name=type_str)
