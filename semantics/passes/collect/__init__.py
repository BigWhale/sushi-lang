# semantics/passes/collect/__init__.py
"""Phase 0 collection pass - orchestrates all collectors via facade pattern.

This module maintains backward compatibility while delegating to specialized collectors.
"""

from __future__ import annotations
from typing import Optional, Set, Tuple

from internals.report import Reporter
from semantics.ast import Program
from semantics.typesys import (
    Type,
    BuiltinType,
    EnumVariantInfo,
    PointerType,
    DynamicArrayType,
)
from semantics.generics.types import (
    TypeParameter,
    GenericEnumType,
    GenericStructType,
)

# Import specialized collectors
from .constants import ConstantCollector, ConstantTable, ConstSig
from .structs import StructCollector, StructTable, GenericStructTable
from .enums import EnumCollector, EnumTable, GenericEnumTable
from .functions import (
    FunctionCollector,
    FunctionTable,
    GenericFunctionTable,
    ExtensionTable,
    GenericExtensionTable,
    FuncSig,
    GenericFuncDef,
    Param,
    ExtensionMethod,
    GenericExtensionMethod,
)
from .perks import PerkCollector, PerkTable, PerkImplementationTable
from .utils import extract_type_param_names

# Re-export all public classes for backward compatibility
__all__ = [
    # Main facade
    'CollectorPass',
    # Tables
    'ConstantTable',
    'StructTable',
    'GenericStructTable',
    'EnumTable',
    'GenericEnumTable',
    'FunctionTable',
    'GenericFunctionTable',
    'ExtensionTable',
    'GenericExtensionTable',
    'PerkTable',
    'PerkImplementationTable',
    # Signatures
    'ConstSig',
    'FuncSig',
    'GenericFuncDef',
    'Param',
    'ExtensionMethod',
    'GenericExtensionMethod',
    # Utilities
    'extract_type_param_names',
]


class CollectorPass:
    """Phase 0: Collect constants, structs, enums, functions, and perks from the AST.

    This facade orchestrates specialized collectors while maintaining backward-compatible API.
    Collects:
    - Constants
    - Struct definitions (regular and generic)
    - Enum definitions (regular and generic)
    - Function signatures (concrete and generic)
    - Extension methods (regular and generic)
    - Perk definitions and implementations

    All collectors run independently but share table references for cross-validation.
    """

    def __init__(self, reporter: Reporter) -> None:
        """Initialize collector pass with all sub-collectors.

        Args:
            reporter: Error reporter for diagnostics
        """
        self.r = reporter

        # Initialize shared tables (these will be populated by collectors)
        self.constants = ConstantTable()
        self.structs = StructTable()
        self.generic_structs = GenericStructTable()
        self.enums = EnumTable()
        self.generic_enums = GenericEnumTable()
        self.funcs = FunctionTable()
        self.generic_funcs = GenericFunctionTable()
        self.extensions = ExtensionTable()
        self.generic_extensions = GenericExtensionTable()
        self.perks = PerkTable()
        self.perk_impls = PerkImplementationTable()

        # Known types set (shared across collectors)
        self.known_types: Set[Type] = {
            BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
            BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
            BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
        }

        # Initialize specialized collectors with shared table references
        self.constant_collector = ConstantCollector(
            reporter=reporter,
            constants=self.constants
        )

        self.struct_collector = StructCollector(
            reporter=reporter,
            structs=self.structs,
            generic_structs=self.generic_structs,
            known_types=self.known_types
        )

        self.enum_collector = EnumCollector(
            reporter=reporter,
            enums=self.enums,
            generic_enums=self.generic_enums,
            structs=self.structs,
            generic_structs=self.generic_structs,
            known_types=self.known_types
        )

        self.perk_collector = PerkCollector(
            reporter=reporter,
            perks=self.perks,
            perk_impls=self.perk_impls
        )

        self.function_collector = FunctionCollector(
            reporter=reporter,
            funcs=self.funcs,
            generic_funcs=self.generic_funcs,
            extensions=self.extensions,
            generic_extensions=self.generic_extensions,
            structs=self.structs,
            enums=self.enums,
            generic_structs=self.generic_structs,
            generic_enums=self.generic_enums
        )

        # Register predefined types (must happen after collectors initialized)
        self._register_predefined_enums()
        self._register_predefined_generics()

    def run(self, root: Program, unit_name: Optional[str] = None) -> Tuple[
        ConstantTable,
        StructTable,
        EnumTable,
        GenericEnumTable,
        GenericStructTable,
        PerkTable,
        PerkImplementationTable,
        FunctionTable,
        ExtensionTable,
        GenericExtensionTable,
        GenericFunctionTable
    ]:
        """Run all collection passes in dependency order.

        Args:
            root: Program AST node
            unit_name: Optional unit name for multi-file compilation

        Returns:
            Tuple of all collected tables (for backward compatibility)
        """
        # Collect in dependency order
        self.constant_collector.collect(root)
        self.struct_collector.collect(root)
        self.enum_collector.collect(root)
        self.perk_collector.collect_definitions(root)
        self.perk_collector.collect_implementations(root)
        self.perk_collector.register_synthetic_impls()
        self.function_collector.collect_functions(root, unit_name)
        self.function_collector.collect_extensions(root)
        self.function_collector.register_stdlib_functions(root)

        return (
            self.constants,
            self.structs,
            self.enums,
            self.generic_enums,
            self.generic_structs,
            self.perks,
            self.perk_impls,
            self.funcs,
            self.extensions,
            self.generic_extensions,
            self.generic_funcs
        )

    def _register_predefined_enums(self) -> None:
        """Register predefined enums (FileMode, FileResult, etc.).

        Delegates to enum collector for actual registration.
        """
        self.enum_collector.register_predefined_enums()

    def _register_predefined_generics(self) -> None:
        """Register predefined generic enums and structs.

        These generic types are built into the language and available globally:

        Generic Enums:
        - Result<T>: Generic error handling type with Ok(T) and Err() variants
        - Maybe<T>: Optional values with Some(T) and None() variants

        Generic Structs:
        - Own<T>: Unique ownership of heap-allocated data (for recursive types)
        - HashMap<K, V>: Hash table with open addressing
        - List<T>: Dynamic array with automatic growth

        Implementation Status:
        ✅ Result<T> is fully implemented and working (Phase 6.1)
        - All functions implicitly return Result<T> where T is the declared return type
        - Supports .realise(default) method for safe unwrapping
        - Supports if (result) conditional syntax
        - Comprehensive compiler enforcement (CE2502-CE2505 errors)

        ✅ Maybe<T> is fully implemented and working (Phase 1)
        - Optional values with Some(T) and None() variants
        - Supports .is_some(), .is_none(), .realise(default), .expect(message) methods
        - Pattern matching for safe value extraction

        ✅ Own<T> is being implemented (Phase 2 - Recursive Types)
        - Unique ownership of heap-allocated data
        - Supports .new(value), .get(), .destroy() methods
        - Enables recursive enum types

        Future Generic Types (Not Yet Implemented):
        - Pair<T, U>: Tuple-like pairing
        - User-defined generic enums beyond built-ins (grammar support exists)
        """
        # Result<T, E> generic enum - error handling with typed errors
        # Type parameters: T (success value type), E (error type)
        # Variants:
        #   Ok(T) - success with value of type T
        #   Err(E) - failure with error of type E
        result_generic = GenericEnumType(
            name="Result",
            type_params=(TypeParameter(name="T"), TypeParameter(name="E")),
            variants=(
                EnumVariantInfo(
                    name="Ok",
                    # Ok variant holds a value of type T (the generic parameter)
                    # We use TypeParameter("T") to represent the generic type
                    associated_types=(TypeParameter(name="T"),)
                ),
                EnumVariantInfo(
                    name="Err",
                    # Err variant holds an error of type E (the generic parameter)
                    associated_types=(TypeParameter(name="E"),)
                ),
            )
        )
        self.generic_enums.by_name["Result"] = result_generic
        self.generic_enums.order.append("Result")

        # Maybe<T> generic enum - optional values
        # Type parameter: T (the value type when present)
        # Variants:
        #   Some(T) - contains a value of type T
        #   None() - no value present
        maybe_generic = GenericEnumType(
            name="Maybe",
            type_params=(TypeParameter(name="T"),),
            variants=(
                EnumVariantInfo(
                    name="Some",
                    # Some variant holds a value of type T (the generic parameter)
                    associated_types=(TypeParameter(name="T"),)
                ),
                EnumVariantInfo(
                    name="None",
                    # None variant has no associated data
                    associated_types=()
                ),
            )
        )
        self.generic_enums.by_name["Maybe"] = maybe_generic
        self.generic_enums.order.append("Maybe")

        # Own<T> generic struct - unique ownership of heap-allocated data
        # Type parameter: T (the owned value type)
        # Fields:
        #   value: T* (pointer to heap-allocated T)
        # Note: The actual field is a PointerType, but we represent it internally
        own_generic = GenericStructType(
            name="Own",
            type_params=(TypeParameter(name="T"),),
            # Field stores a pointer to T (T*)
            fields=(("value", PointerType(pointee_type=TypeParameter(name="T"))),)
        )
        self.generic_structs.by_name["Own"] = own_generic
        self.generic_structs.order.append("Own")

        # HashMap<K, V> generic struct - hash table with open addressing
        # Only registered if activated via `use <collections/hashmap>`
        from semantics.generics.providers.registry import GenericTypeRegistry
        if GenericTypeRegistry.is_available("HashMap"):
            # Type parameters: K (key type), V (value type)
            # Fields:
            #   buckets: Entry<K, V>[] (dynamic array of hash table entries)
            #   size: i32 (number of occupied entries, excludes tombstones)
            #   capacity: i32 (total bucket count, always prime for better distribution)
            #   tombstones: i32 (number of deleted entries marked as tombstones)
            #
            # Internal Entry<K, V> structure (not exposed to users):
            #   K key
            #   V value
            #   u8 state (0=Empty, 1=Occupied, 2=Tombstone)
            #
            # Note: Entry<K, V> is managed internally during emission, not defined as a separate type
            hashmap_generic = GenericStructType(
                name="HashMap",
                type_params=(TypeParameter(name="K"), TypeParameter(name="V")),
                # Fields represent the HashMap structure
                # buckets is a placeholder (i32[]) - actual LLVM type is Entry<K,V>[]
                fields=(
                    ("buckets", DynamicArrayType(base_type=BuiltinType.I32)),  # Placeholder for Entry<K,V>[]
                    ("size", BuiltinType.I32),
                    ("capacity", BuiltinType.I32),
                    ("tombstones", BuiltinType.I32),
                )
            )
            self.generic_structs.by_name["HashMap"] = hashmap_generic
            self.generic_structs.order.append("HashMap")

        # List<T> generic struct - dynamic array with automatic growth
        # Type parameters: T (element type)
        # Fields:
        #   len: i32 (current number of elements)
        #   capacity: i32 (allocated capacity)
        #   data: T* (pointer to heap-allocated array)
        #
        # Features:
        #   - Automatic 2x growth on push when len >= capacity
        #   - Lazy allocation (capacity 0 until first push)
        #   - Methods: new(), with_capacity(), push(), pop(), get(), clear(), reserve(), shrink_to_fit(), destroy(), free()
        list_generic = GenericStructType(
            name="List",
            type_params=(TypeParameter(name="T"),),
            # Fields represent the List structure
            # data is a placeholder (i32*) - actual LLVM type is T*
            fields=(
                ("len", BuiltinType.I32),
                ("capacity", BuiltinType.I32),
                ("data", PointerType(BuiltinType.I32)),  # Placeholder for T*
            )
        )
        self.generic_structs.by_name["List"] = list_generic
        self.generic_structs.order.append("List")

        # Note: Generic enums and structs are not added to known_types until they are instantiated with concrete types
