"""
Pass 1.8: Hash Registration Pass

Registers .hash() extension methods for all hashable struct, enum, and array types.

This pass runs AFTER monomorphization (Pass 1.6) because:
1. Generic structs/enums need to be monomorphized first (Pair<T, U> -> Pair<i32, string>)
2. Nested struct/enum types need to be resolved (UnknownType -> StructType/EnumType)
3. We need full type information to determine hashability

This pass runs BEFORE type validation (Pass 2) because:
1. Type validator needs hash methods registered to validate .hash() calls
2. Method resolution happens during type checking
"""

from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.backend.types.structs import can_struct_be_hashed, register_struct_hash_method
from sushi_lang.backend.types.enums import can_enum_be_hashed, register_enum_hash_method
from sushi_lang.backend.types.arrays.methods.hashing import can_array_be_hashed, register_array_hash_method
from sushi_lang.semantics.typesys import StructType, EnumType, ArrayType, DynamicArrayType, Type
from collections import defaultdict, deque
from typing import List, Set, Dict
from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er


def register_all_struct_hashes(struct_table: StructTable) -> None:
    """Register hash methods for all hashable structs in dependency order.

    Uses topological sort to ensure nested structs get their hash registered
    before parent structs that contain them.

    Args:
        struct_table: Table of all struct types (after monomorphization)
    """
    # Get structs in dependency order (dependencies first)
    sorted_structs = topological_sort_structs(struct_table)

    # Register hash for each struct if hashable
    registered_count = 0
    skipped_count = 0

    for struct_name in sorted_structs:
        struct_type = struct_table.by_name[struct_name]
        can_hash, reason = can_struct_be_hashed(struct_type)

        if can_hash:
            register_struct_hash_method(struct_type)
            registered_count += 1
        else:
            skipped_count += 1

    # Minimal output - can be enabled with --debug flag in future
    # Debug info available but not printed by default for cleaner compilation output


def topological_sort_structs(struct_table: StructTable) -> List[str]:
    """Sort struct names in dependency order using Kahn's algorithm.

    A struct A depends on struct B if:
    - A has a field of type B (nested struct)

    Returns structs with no dependencies first, then structs that depend on them.

    Example:
        Point: no dependencies
        Rectangle: depends on Point

    Returns: ["Point", "Rectangle"]

    Args:
        struct_table: Table of all struct types

    Returns:
        List of struct names in dependency order

    Raises:
        ValueError: If circular struct dependency detected
    """
    # Build dependency graph
    dependencies: Dict[str, Set[str]] = defaultdict(set)  # struct -> set of structs it depends on
    dependents: Dict[str, Set[str]] = defaultdict(set)    # struct -> set of structs that depend on it

    for struct_name, struct_type in struct_table.by_name.items():
        # Check each field for struct dependencies
        for field_name, field_type in struct_type.fields:
            if isinstance(field_type, StructType):
                # This struct depends on the field's struct type
                dependencies[struct_name].add(field_type.name)
                dependents[field_type.name].add(struct_name)

    # Kahn's algorithm for topological sort
    in_degree = {name: len(deps) for name, deps in dependencies.items()}

    # Start with structs that have no dependencies
    queue = deque([
        name for name in struct_table.by_name
        if in_degree.get(name, 0) == 0
    ])

    result = []

    while queue:
        current = queue.popleft()
        result.append(current)

        # Reduce in-degree for all dependents
        for dependent in dependents.get(current, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Check for cycles (shouldn't happen with Sushi's type system)
    if len(result) != len(struct_table.by_name):
        # Some structs not processed - indicates circular dependency
        # This shouldn't happen since Sushi doesn't support recursive structs
        unprocessed = set(struct_table.by_name.keys()) - set(result)
        raise ValueError(f"Circular struct dependency detected: {unprocessed}")

    return result


def register_all_enum_hashes(enum_table: EnumTable, reporter: Reporter) -> None:
    """Register hash methods for all hashable enums in dependency order.

    Uses topological sort to ensure nested enums get their hash registered
    before parent enums that contain them.

    Args:
        enum_table: Table of all enum types (after monomorphization)
        reporter: Error reporter for diagnostic messages
    """
    # Get enums in dependency order (dependencies first)
    sorted_enums = topological_sort_enums(enum_table, reporter)

    # Register hash for each enum if hashable
    registered_count = 0
    skipped_count = 0

    for enum_name in sorted_enums:
        enum_type = enum_table.by_name[enum_name]
        can_hash, reason = can_enum_be_hashed(enum_type)

        if can_hash:
            register_enum_hash_method(enum_type)
            registered_count += 1
        else:
            skipped_count += 1

    # Minimal output - can be enabled with --debug flag in future
    # Debug info available but not printed by default for cleaner compilation output


def topological_sort_enums(enum_table: EnumTable, reporter: Reporter) -> List[str]:
    """Sort enum names in dependency order using Kahn's algorithm.

    An enum A depends on enum B if:
    - A has a variant with associated type B (nested enum)

    An enum A depends on struct B if:
    - A has a variant with associated type B (struct in variant data)

    Returns enums with no dependencies first, then enums that depend on them.

    Example:
        Status: no dependencies
        Response: depends on Status

    Returns: ["Status", "Response"]

    Args:
        enum_table: Table of all enum types
        reporter: Error reporter for diagnostic messages

    Returns:
        List of enum names in dependency order
    """
    # Build dependency graph (only for enum-to-enum dependencies)
    dependencies: Dict[str, Set[str]] = defaultdict(set)  # enum -> set of other enums it depends on
    dependents: Dict[str, Set[str]] = defaultdict(set)    # enum -> set of enums that depend on it

    for enum_name, enum_type in enum_table.by_name.items():
        # Check each variant's associated types for enum dependencies
        # Note: We only track enum dependencies, not struct dependencies, because
        # struct hashes are already registered in Pass 1.8 before enum hashes
        for variant in enum_type.variants:
            for assoc_type in variant.associated_types:
                # Track enum dependencies only
                if isinstance(assoc_type, EnumType):
                    dependencies[enum_name].add(assoc_type.name)
                    dependents[assoc_type.name].add(enum_name)

    # Kahn's algorithm for topological sort
    in_degree = {name: len(deps) for name, deps in dependencies.items()}

    # Start with enums that have no dependencies
    queue = deque([
        name for name in enum_table.by_name
        if in_degree.get(name, 0) == 0
    ])

    result = []

    while queue:
        current = queue.popleft()
        result.append(current)

        # Reduce in-degree for all dependents
        for dependent in dependents.get(current, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Check for cycles
    if len(result) != len(enum_table.by_name):
        # Some enums not processed - check if they use Own<T> for recursion
        unprocessed = set(enum_table.by_name.keys()) - set(result)

        # Separate: enums with Own<T> indirection vs direct recursion
        own_recursive = []
        direct_recursive = []

        for enum_name in unprocessed:
            enum_type = enum_table.by_name[enum_name]
            if has_own_indirection(enum_type, enum_name):
                own_recursive.append(enum_name)
            else:
                direct_recursive.append(enum_name)

        # Direct recursion without Own<T> is still an error
        if direct_recursive:
            # Report an error for each directly recursive enum
            for enum_name in sorted(direct_recursive):
                er.emit(reporter, er.ERR.CE2052, None, name=enum_name)

        # Own<T>-based recursion is allowed - add in any order
        result.extend(own_recursive)

    return result


def has_own_indirection(enum_type: EnumType, enum_name: str) -> bool:
    """Check if recursive enum uses Own<T> for indirection.

    Returns True if the enum references itself through Own<T> wrapper.

    Example:
        enum Expr:
            IntLit(i32)
            BinOp(Own<Expr>, Own<Expr>, string)

        has_own_indirection(Expr, "Expr") -> True

    Args:
        enum_type: The enum type to check
        enum_name: The name of the enum being checked

    Returns:
        True if the enum uses Own<T> to reference itself, False otherwise
    """
    for variant in enum_type.variants:
        for assoc_type in variant.associated_types:
            # Check if field is Own<T>
            if _is_own_type(assoc_type):
                # Check if Own<T> wraps the enum itself
                element_type = _get_own_element_type(assoc_type)
                if _references_enum(element_type, enum_name):
                    return True

    return False


def _is_own_type(ty: Type) -> bool:
    """Check if type is Own<T>."""
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if isinstance(ty, StructType):
        return ty.name.startswith("Own<")
    elif isinstance(ty, GenericTypeRef):
        return ty.base_name == "Own"
    return False


def _get_own_element_type(own_type: Type) -> Type:
    """Extract T from Own<T>."""
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.typesys import UnknownType

    if isinstance(own_type, GenericTypeRef):
        # Before monomorphization: extract from type_args
        if len(own_type.type_args) == 1:
            return own_type.type_args[0]
    elif isinstance(own_type, StructType):
        # After monomorphization: parse from name "Own<Expr>"
        if own_type.name.startswith("Own<") and own_type.name.endswith(">"):
            inner_name = own_type.name[4:-1]  # Extract "Expr" from "Own<Expr>"
            return UnknownType(name=inner_name)

    # Fallback
    return UnknownType(name="Unknown")


def _references_enum(ty: Type, enum_name: str) -> bool:
    """Check if type references an enum (directly or in array).

    Args:
        ty: The type to check
        enum_name: The name of the enum to look for

    Returns:
        True if ty references the enum with the given name
    """
    from sushi_lang.semantics.typesys import UnknownType

    if isinstance(ty, EnumType):
        # Direct reference
        return ty.name == enum_name
    elif isinstance(ty, DynamicArrayType):
        # Array of enums
        return _references_enum(ty.base_type, enum_name)
    elif isinstance(ty, ArrayType):
        # Fixed array of enums
        return _references_enum(ty.base_type, enum_name)
    elif isinstance(ty, UnknownType):
        # Unresolved type - check by name
        return ty.name == enum_name

    return False


def collect_array_types(struct_table: StructTable, enum_table: EnumTable) -> Set[Type]:
    """Collect all array types used in structs, enums, and HashMap keys/values.

    This finds array types in:
    - Struct field types
    - Enum variant associated types
    - HashMap key types (inferred from HashMap struct field types)
    - HashMap value types (inferred from HashMap struct field types)

    Args:
        struct_table: Table of all struct types
        enum_table: Table of all enum types

    Returns:
        Set of unique array types (ArrayType and DynamicArrayType instances)
    """
    array_types: Set[Type] = set()

    def extract_arrays_from_type(ty: Type) -> None:
        """Recursively extract array types from a type."""
        if isinstance(ty, (ArrayType, DynamicArrayType)):
            array_types.add(ty)

    # Collect from struct fields
    for struct_type in struct_table.by_name.values():
        for field_name, field_type in struct_type.fields:
            extract_arrays_from_type(field_type)

    # Collect from enum variant data
    for enum_type in enum_table.by_name.values():
        for variant in enum_type.variants:
            for assoc_type in variant.associated_types:
                extract_arrays_from_type(assoc_type)

    return array_types


def register_all_array_hashes(struct_table: StructTable, enum_table: EnumTable) -> None:
    """Register hash methods for all hashable array types.

    This must run AFTER struct and enum hashes are registered, because array
    hashing depends on element type hashing.

    Args:
        struct_table: Table of all struct types
        enum_table: Table of all enum types
    """
    # Collect all array types used in the program
    array_types = collect_array_types(struct_table, enum_table)

    # Register hash for each array if element type is hashable
    registered_count = 0
    skipped_count = 0

    for array_type in array_types:
        can_hash, reason = can_array_be_hashed(array_type)

        if can_hash:
            register_array_hash_method(array_type)
            registered_count += 1
        else:
            skipped_count += 1

    # Minimal output - can be enabled with --debug flag in future
    # Debug info available but not printed by default for cleaner compilation output
