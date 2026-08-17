"""Pass 1.8: Hash Registration Pass"""

from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.semantics.generics.hashing import can_struct_be_hashed, register_struct_hash_method
from sushi_lang.semantics.generics.hashing import can_enum_be_hashed, register_enum_hash_method
from sushi_lang.semantics.generics.hashing import can_array_be_hashed, register_array_hash_method
from sushi_lang.semantics.typesys import StructType, EnumType, ArrayType, DynamicArrayType, Type
from collections import defaultdict, deque
from typing import List, Set, Dict
from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error


def register_all_struct_hashes(struct_table: StructTable) -> None:
    """Register hash methods for all hashable structs in dependency order."""
    sorted_structs = topological_sort_structs(struct_table)

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


def topological_sort_structs(struct_table: StructTable) -> List[str]:
    """Sort struct names in dependency order using Kahn's algorithm."""
    dependencies: Dict[str, Set[str]] = defaultdict(set)  # struct -> set of structs it depends on
    dependents: Dict[str, Set[str]] = defaultdict(set)    # struct -> set of structs that depend on it

    for struct_name, struct_type in struct_table.by_name.items():
        for _field_name, field_type in struct_type.fields:
            if isinstance(field_type, StructType):
                dependencies[struct_name].add(field_type.name)
                dependents[field_type.name].add(struct_name)

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

        for dependent in dependents.get(current, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Unreachable by construction: Pass 1.75 (semantics/passes/infinite_types.py)
    # reports any by-value containment cycle as CE2095 and stops the analysis before
    # this pass runs. A cycle here is a gap in that check, so it fails loud as a
    # registered internal diagnostic rather than the bare ValueError it used to be
    # (which surfaced as a CE0000 "this is a compiler bug" with no explanation).
    if len(result) != len(struct_table.by_name):
        unprocessed = set(struct_table.by_name.keys()) - set(result)
        raise_internal_error("CE0128", names=", ".join(sorted(unprocessed)))

    return result


def register_all_enum_hashes(enum_table: EnumTable, reporter: Reporter) -> None:
    """Register hash methods for all hashable enums in dependency order."""
    sorted_enums = topological_sort_enums(enum_table, reporter)

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


def topological_sort_enums(enum_table: EnumTable, reporter: Reporter) -> List[str]:
    """Sort enum names in dependency order using Kahn's algorithm."""
    dependencies: Dict[str, Set[str]] = defaultdict(set)  # enum -> set of other enums it depends on
    dependents: Dict[str, Set[str]] = defaultdict(set)    # enum -> set of enums that depend on it

    for enum_name, enum_type in enum_table.by_name.items():
        # Check each variant's associated types for enum dependencies
        # Note: We only track enum dependencies, not struct dependencies, because
        # struct hashes are already registered in Pass 1.8 before enum hashes
        for variant in enum_type.variants:
            for assoc_type in variant.associated_types:
                if isinstance(assoc_type, EnumType):
                    dependencies[enum_name].add(assoc_type.name)
                    dependents[assoc_type.name].add(enum_name)

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

        for dependent in dependents.get(current, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(result) != len(enum_table.by_name):
        unprocessed = set(enum_table.by_name.keys()) - set(result)

        own_recursive = []
        direct_recursive = []

        for enum_name in unprocessed:
            enum_type = enum_table.by_name[enum_name]
            if has_own_indirection(enum_type, enum_name):
                own_recursive.append(enum_name)
            else:
                direct_recursive.append(enum_name)

        if direct_recursive:
            for enum_name in sorted(direct_recursive):
                er.emit(reporter, er.ERR.CE2052, None, name=enum_name)

        result.extend(own_recursive)

    return result


def has_own_indirection(enum_type: EnumType, enum_name: str) -> bool:
    """Check if recursive enum uses Own<T> for indirection."""
    for variant in enum_type.variants:
        for assoc_type in variant.associated_types:
            if _is_own_type(assoc_type):
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
        if len(own_type.type_args) == 1:
            return own_type.type_args[0]
    elif isinstance(own_type, StructType):
        if own_type.name.startswith("Own<") and own_type.name.endswith(">"):
            inner_name = own_type.name[4:-1]  # Extract "Expr" from "Own<Expr>"
            return UnknownType(name=inner_name)

    return UnknownType(name="Unknown")


def _references_enum(ty: Type, enum_name: str) -> bool:
    """Check if type references an enum (directly or in array)."""
    from sushi_lang.semantics.typesys import UnknownType

    if isinstance(ty, EnumType):
        return ty.name == enum_name
    elif isinstance(ty, DynamicArrayType):
        return _references_enum(ty.base_type, enum_name)
    elif isinstance(ty, ArrayType):
        return _references_enum(ty.base_type, enum_name)
    elif isinstance(ty, UnknownType):
        return ty.name == enum_name

    return False


def collect_array_types(struct_table: StructTable, enum_table: EnumTable) -> Set[Type]:
    """Collect all array types used in structs, enums, and HashMap keys/values."""
    array_types: Set[Type] = set()

    def extract_arrays_from_type(ty: Type) -> None:
        """Recursively extract array types from a type."""
        if isinstance(ty, (ArrayType, DynamicArrayType)):
            array_types.add(ty)

    for struct_type in struct_table.by_name.values():
        for _field_name, field_type in struct_type.fields:
            extract_arrays_from_type(field_type)

    for enum_type in enum_table.by_name.values():
        for variant in enum_type.variants:
            for assoc_type in variant.associated_types:
                extract_arrays_from_type(assoc_type)

    return array_types


def register_all_array_hashes(struct_table: StructTable, enum_table: EnumTable) -> None:
    """Register hash methods for all hashable array types."""
    array_types = collect_array_types(struct_table, enum_table)

    registered_count = 0
    skipped_count = 0

    for array_type in array_types:
        can_hash, reason = can_array_be_hashed(array_type)

        if can_hash:
            register_array_hash_method(array_type)
            registered_count += 1
        else:
            skipped_count += 1

