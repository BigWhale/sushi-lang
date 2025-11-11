# semantics/passes/collect.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from internals.report import Reporter, Span, span_of
from internals import errors as er
from internals.errors import ERR
from semantics.ast import Program, FuncDef, ConstDef, ExtendDef, StructDef, EnumDef, PerkDef, ExtendWithDef, BoundedTypeParam, Block
from semantics.typesys import (
    Type,
    BuiltinType,
    UnknownType,
    ArrayType,
    DynamicArrayType,
    StructType,
    EnumType,
    EnumVariantInfo,
    type_from_rule_name,
    TYPE_NODE_NAMES,
    type_string_from_rule_name,
)
from semantics.generics.types import GenericEnumType, GenericStructType, TypeParameter, GenericTypeRef, TypeParam


def extract_type_param_names(type_params_raw: Optional[List]) -> Optional[List[str]]:
    """Extract type parameter names from AST type_params.

    Handles both legacy List[str] and new List[BoundedTypeParam] formats.

    Args:
        type_params_raw: Raw type_params from AST (may be None, List[str], or List[BoundedTypeParam])

    Returns:
        List of parameter names as strings, or None if no parameters
    """
    if type_params_raw is None:
        return None

    if not isinstance(type_params_raw, list) or len(type_params_raw) == 0:
        return None

    names = []
    for tp in type_params_raw:
        if isinstance(tp, str):
            # Legacy format: direct string
            names.append(tp)
        elif isinstance(tp, BoundedTypeParam):
            # New format: BoundedTypeParam with .name attribute
            names.append(tp.name)
        else:
            # Unknown format - skip
            continue

    return names if names else None


@dataclass
class Param:
    name: str
    ty: Optional[Type]
    name_span: Optional[Span]
    type_span: Optional[Span]
    index: int

@dataclass
class FuncSig:
    """
    Phase 0 function signature.
    - Types are Optional for now to allow defensive collection before full typing.
    """
    name: str
    loc: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_type: Optional[Type] = None
    ret_span: Optional[Span] = None
    params: List[Param] = field(default_factory=list)
    is_public: bool = False              # True if declared with 'public' keyword
    unit_name: Optional[str] = None      # Which unit this function belongs to (for multi-file)

@dataclass
class GenericFuncDef:
    """Generic function definition with type parameters.

    Collected in Pass 0 and stored until monomorphization.
    Similar to GenericStructType/GenericEnumType but for executable functions.
    """
    name: str                                    # Function name (e.g., "compute_hash")
    type_params: tuple[TypeParam, ...]           # Type parameters (TypeParameter or BoundedTypeParam)
    params: List[Param]                          # Parameters (may contain TypeParameter in types)
    ret: Optional[Type]                          # Return type (may be TypeParameter)
    body: Block                                  # Function body (not monomorphized yet)
    is_public: bool = False
    loc: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None

@dataclass
class ConstSig:
    """
    Phase 0 constant signature.
    - Types are Optional for now to allow defensive collection before full typing.
    """
    name: str
    loc: Optional[Span] = None
    name_span: Optional[Span] = None
    const_type: Optional[Type] = None
    type_span: Optional[Span] = None
    # Note: value is validated later in type checking pass

@dataclass
class ConstantTable:
    by_name: Dict[str, ConstSig] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

@dataclass
class FunctionTable:
    by_name: Dict[str, FuncSig] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    # Stdlib functions: (module_path, function_name) -> StdlibFunction
    _stdlib_functions: Dict[Tuple[str, str], Any] = field(default_factory=dict)

    def register_stdlib_function(self, module_path: str, stdlib_func: Any) -> None:
        """
        Register a stdlib function.

        Args:
            module_path: Module path (e.g., "time", "sys/env")
            stdlib_func: StdlibFunction metadata from stdlib_registry
        """
        key = (module_path, stdlib_func.name)
        self._stdlib_functions[key] = stdlib_func

    def lookup_stdlib_function(self, module_path: str, function_name: str) -> Optional[Any]:
        """
        Lookup a stdlib function by module and name.

        Args:
            module_path: Module path (e.g., "time", "sys/env")
            function_name: Function name (e.g., "sleep", "getenv")

        Returns:
            StdlibFunction metadata or None if not found
        """
        return self._stdlib_functions.get((module_path, function_name))

    def is_stdlib_function(self, module_path: str, function_name: str) -> bool:
        """
        Check if a function is a stdlib function.

        Args:
            module_path: Module path (e.g., "time", "sys/env")
            function_name: Function name (e.g., "sleep", "getenv")

        Returns:
            True if function is stdlib, False otherwise
        """
        return (module_path, function_name) in self._stdlib_functions

@dataclass
class GenericFunctionTable:
    """Table of generic function definitions collected in Pass 0.

    Generic functions are stored separately from concrete functions because:
    1. They cannot be called directly (must be instantiated with type arguments)
    2. They need to be monomorphized before code generation
    3. Multiple monomorphized versions can coexist (one per instantiation)
    """
    by_name: Dict[str, GenericFuncDef] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

    def has_function(self, name: str) -> bool:
        """Check if generic function exists."""
        return name in self.by_name

    def get_function(self, name: str) -> Optional[GenericFuncDef]:
        """Lookup generic function by name."""
        return self.by_name.get(name)

@dataclass
class StructTable:
    """Table of struct types collected in Pass 0."""
    by_name: Dict[str, "StructType"] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

@dataclass
class EnumTable:
    """Table of enum types collected in Pass 0."""
    by_name: Dict[str, "EnumType"] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

@dataclass
class GenericEnumTable:
    """Table of generic enum types collected in Pass 0.

    Generic enums are enum definitions with type parameters (e.g., Result<T>).
    They are stored separately from concrete enums because they need to be
    instantiated with concrete type arguments during monomorphization.
    """
    by_name: Dict[str, "GenericEnumType"] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

@dataclass
class GenericStructTable:
    """Table of generic struct types collected in Pass 0.

    Generic structs are struct definitions with type parameters (e.g., Pair<T, U>).
    They are stored separately from concrete structs because they need to be
    instantiated with concrete type arguments during monomorphization.
    """
    by_name: Dict[str, "GenericStructType"] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

@dataclass
class ExtensionMethod:
    """
    Phase 0 extension method signature.
    Similar to FuncSig but includes target type.
    """
    target_type: Optional[Type]  # Type being extended (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool, string)
    name: str                    # Method name (add, multiply, etc.)
    loc: Optional[Span] = None
    target_type_span: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_type: Optional[Type] = None
    ret_span: Optional[Span] = None
    params: List[Param] = field(default_factory=list)  # Parameters excluding implicit 'self'

@dataclass
class ExtensionTable:
    """
    Table of extension methods organized by target type.
    by_type[BuiltinType.I32]["add"] = ExtensionMethod(...)
    """
    by_type: Dict[Type, Dict[str, ExtensionMethod]] = field(default_factory=dict)

    def add_method(self, method: ExtensionMethod) -> None:
        """Add a method to the table, creating type entry if needed."""
        if method.target_type is not None:
            if method.target_type not in self.by_type:
                self.by_type[method.target_type] = {}
            self.by_type[method.target_type][method.name] = method

    def get_method(self, target_type: Type, method_name: str) -> Optional[ExtensionMethod]:
        """Get a specific extension method."""
        return self.by_type.get(target_type, {}).get(method_name)

@dataclass
class GenericExtensionMethod:
    """
    Phase 0 generic extension method signature.
    Extension method on a generic type (e.g., extend HashMap<K, V> get(K key) Maybe<V>).
    """
    base_type_name: str              # Generic type name (e.g., "HashMap", "Box")
    type_params: Tuple[str, ...]     # Type parameter names (e.g., ("K", "V"))
    name: str                        # Method name (get, insert, etc.)
    loc: Optional[Span] = None
    target_type_span: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_type: Optional[Type] = None  # May contain TypeParameter instances
    ret_span: Optional[Span] = None
    params: List[Param] = field(default_factory=list)  # May contain TypeParameter in param types
    body: Optional[Any] = None       # Method body (Block AST node)

@dataclass
class PerkTable:
    """Registry of all defined perks."""
    by_name: Dict[str, PerkDef] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

    def register(self, perk: PerkDef) -> bool:
        """Register a perk. Returns False if duplicate."""
        if perk.name in self.by_name:
            return False
        self.by_name[perk.name] = perk
        self.order.append(perk.name)
        return True

    def get(self, name: str) -> Optional[PerkDef]:
        """Get a perk definition by name."""
        return self.by_name.get(name)

@dataclass
class PerkImplementationTable:
    """Tracks which types implement which perks."""
    # Key: (type_name, perk_name), Value: ExtendWithDef
    implementations: Dict[Tuple[str, str], ExtendWithDef] = field(default_factory=dict)

    # Reverse index: type_name -> set of implemented perk names
    by_type: Dict[str, set[str]] = field(default_factory=dict)

    # Reverse index: perk_name -> set of implementing type names
    by_perk: Dict[str, set[str]] = field(default_factory=dict)

    def register(self, impl: ExtendWithDef, type_name: str) -> bool:
        """Register an implementation. Returns False if duplicate."""
        key = (type_name, impl.perk_name)
        if key in self.implementations:
            return False  # Duplicate implementation

        self.implementations[key] = impl

        # Update indexes
        if type_name not in self.by_type:
            self.by_type[type_name] = set()
        self.by_type[type_name].add(impl.perk_name)

        if impl.perk_name not in self.by_perk:
            self.by_perk[impl.perk_name] = set()
        self.by_perk[impl.perk_name].add(type_name)

        return True

    def implements(self, type_name: str, perk_name: str) -> bool:
        """Check if a type implements a perk."""
        return (type_name, perk_name) in self.implementations

    def get_implementations(self, type_name: str) -> set[str]:
        """Get all perks implemented by a type."""
        return self.by_type.get(type_name, set())

    def get(self, type_name: str, perk_name: str) -> Optional[ExtendWithDef]:
        """Get a specific perk implementation."""
        return self.implementations.get((type_name, perk_name))

    def get_method(self, target_type: 'Type', method_name: str) -> Optional['FuncDef']:
        """Get a specific perk method for a type.

        Searches all perks implemented by the type to find the method.
        Returns the method definition if found, None otherwise.
        """
        # Convert Type to string name for lookup
        from semantics.typesys import BuiltinType, StructType, EnumType

        if isinstance(target_type, BuiltinType):
            type_name = str(target_type)
        elif isinstance(target_type, (StructType, EnumType)):
            type_name = target_type.name
        else:
            return None

        # Check all perks implemented by this type
        perks = self.by_type.get(type_name, set())
        for perk_name in perks:
            impl = self.implementations.get((type_name, perk_name))
            if impl:
                # Search for the method in this implementation
                for method in impl.methods:
                    if method.name == method_name:
                        return method

        return None

    def register_synthetic(self, type_name: str, perk_name: str) -> bool:
        """Register a synthetic perk implementation for primitives.

        Synthetic implementations are used when a primitive type has auto-derived
        methods that satisfy a perk's requirements, but no explicit 'extend...with'
        declaration exists.

        This allows primitives (i32, string, bool, etc.) to work seamlessly with
        generic constraints like T: Hashable.

        Args:
            type_name: Name of the primitive type (e.g., "i32", "string")
            perk_name: Name of the perk being implemented (e.g., "Hashable")

        Returns:
            True if registered successfully, False if already exists
        """
        key = (type_name, perk_name)
        if key in self.implementations:
            return False  # Already registered (explicit or synthetic)

        # Register as synthetic implementation (None indicates synthetic)
        self.implementations[key] = None  # type: ignore

        # Update indexes
        if type_name not in self.by_type:
            self.by_type[type_name] = set()
        self.by_type[type_name].add(perk_name)

        if perk_name not in self.by_perk:
            self.by_perk[perk_name] = set()
        self.by_perk[perk_name].add(type_name)

        return True

@dataclass
class GenericExtensionTable:
    """
    Table of generic extension methods organized by base type name.
    by_type["HashMap"]["get"] = GenericExtensionMethod(...)
    by_type["Box"]["unwrap"] = GenericExtensionMethod(...)
    """
    by_type: Dict[str, Dict[str, GenericExtensionMethod]] = field(default_factory=dict)

    def add_method(self, method: GenericExtensionMethod) -> None:
        """Add a generic extension method to the table."""
        if method.base_type_name not in self.by_type:
            self.by_type[method.base_type_name] = {}
        self.by_type[method.base_type_name][method.name] = method

    def get_method(self, base_type_name: str, method_name: str) -> Optional[GenericExtensionMethod]:
        """Get a specific generic extension method."""
        return self.by_type.get(base_type_name, {}).get(method_name)

    def get_all_methods(self, base_type_name: str) -> Dict[str, GenericExtensionMethod]:
        """Get all generic extension methods for a base type."""
        return self.by_type.get(base_type_name, {})



class CollectorPass:
    """
    Phase 0: collect constants, structs, function headers and extension methods from the AST.
    Collects constants, struct definitions, regular functions and extension method definitions.
    """

    def __init__(self, reporter: Reporter) -> None:
        self.r = reporter
        self.constants: ConstantTable = ConstantTable()
        self.structs: StructTable = StructTable()
        self.enums: EnumTable = EnumTable()
        self.generic_enums: GenericEnumTable = GenericEnumTable()
        self.generic_structs: GenericStructTable = GenericStructTable()
        self.generic_extensions: GenericExtensionTable = GenericExtensionTable()
        self.perks: PerkTable = PerkTable()
        self.perk_impls: PerkImplementationTable = PerkImplementationTable()
        self.funcs: FunctionTable = FunctionTable()
        self.extensions: ExtensionTable = ExtensionTable()
        self.generic_funcs: GenericFunctionTable = GenericFunctionTable()
        self.known_types: set[Type] = {
            BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
            BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
            BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
        }
        # Register predefined enums for file operations
        self._register_predefined_enums()
        # Register predefined generic enums
        self._register_predefined_generics()

    def run(self, root: Program, unit_name: Optional[str] = None) -> Tuple[ConstantTable, StructTable, EnumTable, GenericEnumTable, GenericStructTable, PerkTable, PerkImplementationTable, FunctionTable, ExtensionTable, GenericExtensionTable, GenericFunctionTable]:
        # Collect constants
        constants = getattr(root, "constants", None)
        if isinstance(constants, list):
            for const in constants:
                if isinstance(const, ConstDef):
                    self._collect_constant_def(const)

        # Collect structs
        structs = getattr(root, "structs", None)
        if isinstance(structs, list):
            for struct in structs:
                if isinstance(struct, StructDef):
                    self._collect_struct_def(struct)

        # Collect enums
        enums = getattr(root, "enums", None)
        if isinstance(enums, list):
            for enum in enums:
                if isinstance(enum, EnumDef):
                    self._collect_enum_def(enum)

        # Collect perks
        perks = getattr(root, "perks", None)
        if isinstance(perks, list):
            for perk in perks:
                if isinstance(perk, PerkDef):
                    self._collect_perk_def(perk)

        # Collect perk implementations
        perk_impls = getattr(root, "perk_impls", None)
        if isinstance(perk_impls, list):
            for impl in perk_impls:
                if isinstance(impl, ExtendWithDef):
                    self._collect_perk_impl(impl)

        # Auto-register synthetic perk implementations for primitives
        self._register_synthetic_perk_impls()

        # Collect regular functions
        funcs = getattr(root, "functions", None)
        if isinstance(funcs, list):
            for fn in funcs:
                if isinstance(fn, FuncDef):
                    self._collect_function_def(fn, unit_name=unit_name)

        # Collect non-generic extension methods
        extensions = getattr(root, "extensions", None)
        if isinstance(extensions, list):
            for ext in extensions:
                if isinstance(ext, ExtendDef):
                    self._collect_extension_def(ext)

        # Collect generic extension methods
        generic_extensions = getattr(root, "generic_extensions", None)
        if isinstance(generic_extensions, list):
            for ext in generic_extensions:
                if isinstance(ext, ExtendDef):
                    self._collect_extension_def(ext)

        # Register stdlib functions from imported modules
        self._register_stdlib_functions(root)

        return self.constants, self.structs, self.enums, self.generic_enums, self.generic_structs, self.perks, self.perk_impls, self.funcs, self.extensions, self.generic_extensions, self.generic_funcs

    def _collect_constant_def(self, const: ConstDef) -> None:
        name = getattr(const, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(const, "name_span", None) or getattr(
            const, "loc", None
        )
        const_type: Optional[Type] = getattr(const, "ty", None)
        type_span: Optional[Span] = getattr(const, "type_span", None) or name_span

        # Check for missing type annotation (constants must be explicitly typed)
        if const_type is None:
            er.emit(self.r, ERR.CE0104, name_span, name=name)

        sig = ConstSig(
            name=name,
            name_span=name_span,
            const_type=const_type,
            type_span=type_span,
        )

        # Check for duplicate constant names
        if name in self.constants.by_name:
            prev = self.constants.by_name[name]
            prev_loc = self._format_loc(prev.name_span)
            er.emit(self.r, ERR.CE0105, name_span, name=name, prev_loc=prev_loc)
            return

        self.constants.order.append(name)
        self.constants.by_name[name] = sig

    def _collect_struct_def(self, struct: StructDef) -> None:
        """Collect struct definition and create StructType or GenericStructType."""
        name = getattr(struct, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(struct, "name_span", None) or getattr(struct, "loc", None)

        # Check if this struct has type parameters (e.g., struct Pair<T, U>:)
        type_params_raw = getattr(struct, "type_params", None)
        type_params: Optional[List[str]] = extract_type_param_names(type_params_raw)

        # Check for duplicate struct names (both regular and generic namespaces)
        if name in self.structs.by_name:
            prev = self.structs.by_name[name]
            # Struct types don't have a direct name_span, so use the struct name
            er.emit(self.r, ERR.CE0004, name_span, name=name, prev_loc=str(prev))
            return

        if name in self.generic_structs.by_name:
            # Duplicate with existing generic struct
            er.emit(self.r, ERR.CE0004, name_span, name=name, prev_loc="<predefined generic>")
            return

        # Collect struct fields
        fields_list: List[Tuple[str, Type]] = []
        field_names: set[str] = set()

        struct_fields = getattr(struct, "fields", [])
        for field in struct_fields:
            field_name = getattr(field, "name", None)
            field_type = getattr(field, "ty", None)
            field_loc = getattr(field, "loc", None)

            if not isinstance(field_name, str):
                continue

            # Check for duplicate field names
            if field_name in field_names:
                er.emit(self.r, ERR.CE0005, field_loc, name=field_name, struct_name=name)
                continue

            # Check for missing field type
            if field_type is None:
                er.emit(self.r, ERR.CE0104, field_loc, name=f"field '{field_name}'")
                continue

            # NOTE: Field types may be TypeParameter instances (e.g., T, U) for generic structs
            # These will be resolved during monomorphization
            field_names.add(field_name)
            fields_list.append((field_name, field_type))

        # Branch based on whether this is a generic struct or regular struct
        if type_params and len(type_params) > 0:
            # Generic struct - store in generic_structs table

            # Preserve BoundedTypeParam objects (Phase 4: constraint validation)
            # Convert to tuple, handling both BoundedTypeParam and legacy string formats
            from semantics.ast import BoundedTypeParam
            type_param_instances = tuple(
                tp if isinstance(tp, BoundedTypeParam)
                else TypeParameter(name=tp) if isinstance(tp, TypeParameter)
                else BoundedTypeParam(name=tp, constraints=[], loc=None)
                for tp in type_params_raw
            )

            generic_struct = GenericStructType(
                name=name,
                type_params=type_param_instances,
                fields=tuple(fields_list)
            )

            self.generic_structs.order.append(name)
            self.generic_structs.by_name[name] = generic_struct

            # Note: Generic structs are not added to known_types until instantiated
        else:
            # Regular struct - store in structs table (existing behavior)
            struct_type = StructType(
                name=name,
                fields=tuple(fields_list)
            )

            self.structs.order.append(name)
            self.structs.by_name[name] = struct_type

            # Register struct type as known type for future lookups
            self.known_types.add(struct_type)

            # Hash registration is deferred to Pass 1.8 (hash_registration.py)
            # This ensures all types are resolved (Pass 1.7) and generics are monomorphized (Pass 1.6)
            # before we attempt to register hash methods

    def _collect_enum_def(self, enum: EnumDef) -> None:
        """Collect enum definition and create EnumType or GenericEnumType.

        If the enum has type_params (e.g., enum Result<T>:), it is stored as a
        GenericEnumType in the generic_enums table. Otherwise, it is stored as
        a regular EnumType in the enums table.

        Note: For Phase 0, the grammar does not support user-defined generic enums yet.
        This code is defensive and prepares for future phases when the grammar will
        support enum Result<T>: syntax.
        """
        name = getattr(enum, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(enum, "name_span", None) or getattr(enum, "loc", None)

        # Check if this enum has type parameters (e.g., enum Result<T>:)
        # Note: For Phase 0, type_params will always be None since the grammar doesn't support it yet
        type_params_raw = getattr(enum, "type_params", None)
        type_params: Optional[List[str]] = extract_type_param_names(type_params_raw)

        # Check for duplicate enum names (both regular and generic namespaces)
        if name in self.enums.by_name:
            prev = self.enums.by_name[name]
            # Use CE2046 for duplicate enum error
            er.emit(self.r, ERR.CE2046, name_span, name=name, prev_loc=str(prev))
            return

        if name in self.structs.by_name:
            prev = self.structs.by_name[name]
            er.emit(self.r, ERR.CE0006, name_span, name=name, prev_loc=str(prev))
            return

        if name in self.generic_structs.by_name:
            er.emit(self.r, ERR.CE0006, name_span, name=name, prev_loc="<predefined generic>")
            return

        if name in self.generic_enums.by_name:
            # Duplicate with existing generic enum
            er.emit(self.r, ERR.CE2046, name_span, name=name, prev_loc="<predefined generic>")
            return

        # Collect enum variants
        variants_list: List[EnumVariantInfo] = []
        variant_names: set[str] = set()

        enum_variants = getattr(enum, "variants", [])
        for variant in enum_variants:
            variant_name = getattr(variant, "name", None)
            variant_types = getattr(variant, "associated_types", [])
            variant_loc = getattr(variant, "loc", None)

            if not isinstance(variant_name, str):
                continue

            # Check for duplicate variant names
            if variant_name in variant_names:
                er.emit(self.r, ERR.CE2047, variant_loc, name=variant_name, enum_name=name)
                continue

            # Convert associated types list to tuple
            if variant_types is None:
                variant_types = []

            variant_names.add(variant_name)
            variants_list.append(EnumVariantInfo(
                name=variant_name,
                associated_types=tuple(variant_types)
            ))

        # Branch based on whether this is a generic enum or regular enum
        if type_params and len(type_params) > 0:
            # Generic enum - store in generic_enums table
            # Preserve BoundedTypeParam objects (Phase 4: constraint validation)
            # Convert to tuple, handling both BoundedTypeParam and legacy string formats
            from semantics.ast import BoundedTypeParam
            type_param_instances = tuple(
                tp if isinstance(tp, BoundedTypeParam)
                else TypeParameter(name=tp) if isinstance(tp, TypeParameter)
                else BoundedTypeParam(name=tp, constraints=[], loc=None)
                for tp in type_params_raw
            )

            generic_enum = GenericEnumType(
                name=name,
                type_params=type_param_instances,
                variants=tuple(variants_list)
            )

            self.generic_enums.order.append(name)
            self.generic_enums.by_name[name] = generic_enum

            # Note: Generic enums are not added to known_types until instantiated
        else:
            # Regular enum - store in enums table (existing behavior)
            enum_type = EnumType(
                name=name,
                variants=tuple(variants_list)
            )

            self.enums.order.append(name)
            self.enums.by_name[name] = enum_type

            # Register enum type as known type for future lookups
            self.known_types.add(enum_type)

    def _collect_perk_def(self, perk: PerkDef) -> None:
        """Collect perk definition and register in perk table."""
        name = getattr(perk, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(perk, "name_span", None) or getattr(perk, "loc", None)

        # Check for duplicate perk names
        if not self.perks.register(perk):
            prev = self.perks.get(name)
            prev_loc = self._format_loc(getattr(prev, "name_span", None)) if prev else "unknown"
            er.emit(self.r, ERR.CE4001, name_span, name=name)
            return

    def _collect_perk_impl(self, impl: ExtendWithDef) -> None:
        """Collect perk implementation and register in implementation table."""
        perk_name = getattr(impl, "perk_name", None)
        if not isinstance(perk_name, str):
            return

        perk_name_span: Optional[Span] = getattr(impl, "perk_name_span", None) or getattr(impl, "loc", None)
        target_type: Optional[Type] = getattr(impl, "target_type", None)

        # Extract type name from target type
        type_name = self._get_type_name(target_type)
        if type_name is None:
            # Can't determine type name, skip
            return

        # Check if perk exists
        if not self.perks.get(perk_name):
            er.emit(self.r, ERR.CE4003, perk_name_span, perk=perk_name)
            return

        # Register the implementation
        if not self.perk_impls.register(impl, type_name):
            # Duplicate implementation
            er.emit(self.r, ERR.CE4002, getattr(impl, "loc", None), type=type_name, perk=perk_name)
            return

    def _register_synthetic_perk_impls(self) -> None:
        """Auto-register synthetic perk implementations for primitive types.

        This method checks which perks are defined and automatically registers
        primitives that have the required auto-derived methods.

        Currently supports:
        - Hashable perk: Registers all primitives with auto-derived hash() methods
          (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool, string)

        This allows primitives to work seamlessly with generic constraints
        (e.g., fn compute_hash<T: Hashable>(T value)) without requiring
        explicit 'extend i32 with Hashable' declarations.
        """
        # Primitives with auto-derived hash() methods
        # See: backend/types/primitives/hashing.py
        hashable_primitives = [
            "i8", "i16", "i32", "i64",
            "u8", "u16", "u32", "u64",
            "f32", "f64", "bool", "string"
        ]

        # Check if Hashable perk is defined
        hashable_perk = self.perks.get("Hashable")
        if hashable_perk:
            # Verify perk requires hash() method
            has_hash_method = any(
                method.name == "hash" and method.ret == BuiltinType.U64
                for method in hashable_perk.methods
            )

            if has_hash_method:
                # Register all hashable primitives
                for prim_type in hashable_primitives:
                    self.perk_impls.register_synthetic(prim_type, "Hashable")

    def _get_type_name(self, ty: Optional[Type]) -> Optional[str]:
        """Extract a string name from a Type for use in perk implementation tables."""
        if ty is None:
            return None

        # Handle built-in types
        if isinstance(ty, BuiltinType):
            return str(ty)

        # Handle struct types
        if isinstance(ty, StructType):
            return ty.name

        # Handle enum types
        if isinstance(ty, EnumType):
            return ty.name

        # Handle generic type references (e.g., List<i32>)
        if isinstance(ty, GenericTypeRef):
            return f"{ty.base_name}<{','.join(str(arg) for arg in ty.type_args)}>"

        # Fallback to string representation
        return str(ty)

    def _collect_function_def(self, fn: FuncDef, unit_name: Optional[str] = None) -> None:
        name = getattr(fn, "name", None)
        if not isinstance(name, str):
            return

        # Check if function has type parameters (generic function)
        type_params_raw = getattr(fn, "type_params", None)
        type_params = extract_type_param_names(type_params_raw)

        if type_params and len(type_params) > 0:
            # Generic function - collect separately
            self._collect_generic_function_def(fn, type_params_raw, unit_name)
        else:
            # Regular function - collect as concrete
            self._collect_concrete_function_def(fn, unit_name)

    def _collect_concrete_function_def(self, fn: FuncDef, unit_name: Optional[str] = None) -> None:
        """Collect concrete (non-generic) function definition."""
        name = getattr(fn, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(fn, "name_span", None) or getattr(
            fn, "loc", None
        )
        ret_ty: Optional[Type] = getattr(fn, "ret", None)
        ret_span: Optional[Span] = getattr(fn, "ret_span", None) or name_span
        is_public: bool = getattr(fn, "is_public", False)

        # Check for missing return type
        if ret_ty is None:
            er.emit(self.r, ERR.CE0103, name_span, name=name)

        params: List[Param] = []
        param_names: set[str] = set()
        for idx, p in enumerate(getattr(fn, "params", []) or []):
            param = self._param_from_node(p, idx)

            # Check for duplicate parameter names
            if param.name in param_names:
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            else:
                param_names.add(param.name)

            params.append(param)

        # Check for duplicates in ALL function tables
        if name in self.funcs.by_name:
            prev = self.funcs.by_name[name]
            prev_loc = self._format_loc(prev.name_span)
            er.emit(self.r, ERR.CE0101, name_span, name=name, prev_loc=prev_loc)
            return

        if name in self.generic_funcs.by_name:
            prev = self.generic_funcs.by_name[name]
            prev_loc = self._format_loc(prev.name_span)
            er.emit(self.r, ERR.CE0101, name_span, name=name, prev_loc=prev_loc)
            return

        sig = FuncSig(
            name=name,
            name_span=name_span,
            ret_type=ret_ty,
            ret_span=ret_span,
            params=params,
            is_public=is_public,
            unit_name=unit_name,
        )

        # Special validation for main() function - must return integer type
        if name == "main" and ret_ty is not None:
            # Check if return type is an integer type (i8-i64, u8-u64)
            valid_integer_types = {
                BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64
            }
            if ret_ty not in valid_integer_types:
                er.emit(self.r, ERR.CE0106, ret_span, type=str(ret_ty))

        self.funcs.order.append(name)
        self.funcs.by_name[name] = sig

    def _collect_generic_function_def(
        self,
        fn: FuncDef,
        type_params_raw: List,
        unit_name: Optional[str] = None
    ) -> None:
        """Collect generic function definition.

        Generic functions are stored separately from concrete functions and
        will be monomorphized in Pass 1.6 when their instantiations are detected.
        """
        name = fn.name
        name_span = getattr(fn, "name_span", None) or getattr(fn, "loc", None)

        # Check for duplicates in ALL function tables
        if name in self.generic_funcs.by_name:
            prev = self.generic_funcs.by_name[name]
            prev_loc = self._format_loc(prev.name_span)
            er.emit(self.r, ERR.CE0101, name_span, name=name, prev_loc=prev_loc)
            return

        if name in self.funcs.by_name:
            prev = self.funcs.by_name[name]
            prev_loc = self._format_loc(prev.name_span)
            er.emit(self.r, ERR.CE0101, name_span, name=name, prev_loc=prev_loc)
            return

        # Preserve BoundedTypeParam objects for perk constraints
        type_param_instances = tuple(
            tp if isinstance(tp, BoundedTypeParam)
            else BoundedTypeParam(name=tp, constraints=[], loc=None)
            for tp in type_params_raw
        )

        # Collect parameters (may contain TypeParameter in types)
        params = []
        param_names = set()
        for idx, p in enumerate(getattr(fn, "params", []) or []):
            param = self._param_from_node(p, idx)

            if param.name in param_names:
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            else:
                param_names.add(param.name)

            params.append(param)

        # Get return type
        ret_ty = getattr(fn, "ret", None)
        ret_span = getattr(fn, "ret_span", None) or name_span

        if ret_ty is None:
            er.emit(self.r, ERR.CE0103, name_span, name=name)

        # Get body (should always exist if grammar is correct)
        body = getattr(fn, "body", None)
        if body is None:
            # Defensive: skip if body is missing (shouldn't happen with correct grammar)
            return

        # Create GenericFuncDef
        generic_func = GenericFuncDef(
            name=name,
            type_params=type_param_instances,
            params=params,
            ret=ret_ty,
            body=body,
            is_public=getattr(fn, "is_public", False),
            loc=getattr(fn, "loc", None),
            name_span=name_span,
            ret_span=ret_span
        )

        # Store in generic function table
        self.generic_funcs.order.append(name)
        self.generic_funcs.by_name[name] = generic_func

    def _collect_extension_def(self, ext: ExtendDef) -> None:
        """Collect extension method definition (both regular and generic)."""
        name = getattr(ext, "name", None)
        if not isinstance(name, str):
            return

        target_type: Optional[Type] = getattr(ext, "target_type", None)
        name_span: Optional[Span] = getattr(ext, "name_span", None) or getattr(ext, "loc", None)
        target_type_span: Optional[Span] = getattr(ext, "target_type_span", None)
        ret_ty: Optional[Type] = getattr(ext, "ret", None)
        ret_span: Optional[Span] = getattr(ext, "ret_span", None) or name_span
        body = getattr(ext, "body", None)

        # Check for missing return type
        if ret_ty is None:
            er.emit(self.r, ERR.CE0103, name_span, name=f"extension method '{name}'")

        # Collect parameters (excluding implicit 'self')
        params: List[Param] = []
        param_names: set[str] = set()
        for idx, p in enumerate(getattr(ext, "params", []) or []):
            param = self._param_from_node(p, idx)

            # Check for duplicate parameter names (and implicit conflict with 'self')
            if param.name == "self":
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            elif param.name in param_names:
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            else:
                param_names.add(param.name)

            params.append(param)

        # Branch: Is this a generic extension method?
        if target_type is not None and isinstance(target_type, GenericTypeRef):
            # Generic extension method (e.g., extend Box<T> unwrap() T)
            base_type_name = target_type.base_name
            type_params_tuple = tuple(str(t) if isinstance(t, TypeParameter) else str(t) for t in target_type.type_args)

            # Check if the target type refers to a known generic struct or enum
            if base_type_name not in self.generic_structs.by_name and base_type_name not in self.generic_enums.by_name:
                # Unknown generic type - will be validated in Pass 2
                pass

            # Convert UnknownType to TypeParameter for type parameter names
            # Build set of type parameter names from target type
            type_param_names = set(type_params_tuple)

            def convert_unknown_to_typeparam(ty: Optional[Type]) -> Optional[Type]:
                """Convert UnknownType to TypeParameter if it matches a type parameter name."""
                if ty is None:
                    return None
                if isinstance(ty, UnknownType) and ty.name in type_param_names:
                    return TypeParameter(name=ty.name)
                return ty

            # Convert ret_type and param types
            concrete_ret_ty = convert_unknown_to_typeparam(ret_ty)
            concrete_params = []
            for param in params:
                concrete_param_ty = convert_unknown_to_typeparam(param.ty)
                concrete_params.append(Param(
                    name=param.name,
                    ty=concrete_param_ty,
                    name_span=param.name_span,
                    type_span=param.type_span,
                    index=param.index
                ))

            generic_method = GenericExtensionMethod(
                base_type_name=base_type_name,
                type_params=type_params_tuple,
                name=name,
                loc=getattr(ext, "loc", None),
                target_type_span=target_type_span,
                name_span=name_span,
                ret_type=concrete_ret_ty,
                ret_span=ret_span,
                params=concrete_params,
                body=body,
            )

            # Check for duplicate generic extension methods
            existing = self.generic_extensions.get_method(base_type_name, name)
            if existing is not None:
                prev_loc = self._format_loc(existing.name_span)
                er.emit(self.r, ERR.CE0101, name_span,
                       name=f"extension method '{name}' for '{base_type_name}<...>'",
                       prev_loc=prev_loc)
                return

            self.generic_extensions.add_method(generic_method)
        else:
            # Regular extension method (existing behavior)
            # Resolve UnknownType to StructType/EnumType if possible
            resolved_type = target_type
            if target_type is not None and isinstance(target_type, UnknownType):
                type_name = target_type.name
                # Check if it's a struct
                if type_name in self.structs.by_name:
                    resolved_type = self.structs.by_name[type_name]
                # Check if it's an enum
                elif type_name in self.enums.by_name:
                    resolved_type = self.enums.by_name[type_name]
                # Otherwise, keep as UnknownType and it will be validated in Pass 2

            method = ExtensionMethod(
                target_type=resolved_type,
                name=name,
                loc=getattr(ext, "loc", None),
                target_type_span=target_type_span,
                name_span=name_span,
                ret_type=ret_ty,
                ret_span=ret_span,
                params=params,
            )

            # Check for duplicate extension methods on the same type (only for known types)
            if resolved_type is not None and isinstance(resolved_type, (BuiltinType, ArrayType, StructType, EnumType)):
                existing = self.extensions.get_method(resolved_type, name)
                if existing is not None:
                    prev_loc = self._format_loc(existing.name_span)
                    er.emit(self.r, ERR.CE0101, name_span,
                           name=f"extension method '{name}' for '{resolved_type}'",
                           prev_loc=prev_loc)
                    return

            # Add method to table (skip duplicate checking for unknown types)
            if resolved_type is not None and isinstance(resolved_type, (BuiltinType, ArrayType, StructType, EnumType)):
                self.extensions.add_method(method)

    @staticmethod
    def _param_from_node(p: Any, idx: int) -> Param:
        # Expect object-style params with .name/.ty and optional spans
        pname = getattr(p, "name", None)
        pty: Optional[Type] = getattr(p, "ty", None)
        pname_span: Optional[Span] = getattr(p, "name_span", None)
        ptype_span: Optional[Span] = getattr(p, "type_span", None)

        # Defensive fallbacks
        if not isinstance(pname, str):
            pname = str(pname) if pname is not None else f"_p{idx}"

        return Param(
            name=pname,
            ty=pty,
            name_span=pname_span,
            type_span=ptype_span,
            index=idx,
        )

    def _format_loc(self, span: Optional[Span]) -> str:
        if not span:
            return self.r.filename
        return f"{self.r.filename}:{span.line}:{span.col}"

    def _register_predefined_enums(self) -> None:
        """Register predefined enums for file operations.

        These enums are built into the language and available globally:
        - FileMode: File open modes (Read, Write, Append, ReadB, WriteB, AppendB)
        - SeekFrom: Seek origins (Start, Current, End)
        - FileResult: Result type for open() with Ok(file) and Err() variants

        Note: FileResult uses Ok/Err variant names (not Success/Error) which is
        consistent with Result<T> naming. There is no token conflict because
        variants are always qualified with the enum name (FileResult.Ok vs Result.Ok).
        """
        # FileMode enum - file open modes
        file_mode_enum = EnumType(
            name="FileMode",
            variants=(
                EnumVariantInfo(name="Read", associated_types=()),      # Text read mode ("r")
                EnumVariantInfo(name="Write", associated_types=()),     # Text write mode ("w")
                EnumVariantInfo(name="Append", associated_types=()),    # Text append mode ("a")
                EnumVariantInfo(name="ReadB", associated_types=()),     # Binary read mode ("rb")
                EnumVariantInfo(name="WriteB", associated_types=()),    # Binary write mode ("wb")
                EnumVariantInfo(name="AppendB", associated_types=()),   # Binary append mode ("ab")
            )
        )
        self.enums.by_name["FileMode"] = file_mode_enum
        self.enums.order.append("FileMode")
        self.known_types.add(file_mode_enum)

        # SeekFrom enum - seek origins
        seek_from_enum = EnumType(
            name="SeekFrom",
            variants=(
                EnumVariantInfo(name="Start", associated_types=()),     # SEEK_SET (0)
                EnumVariantInfo(name="Current", associated_types=()),   # SEEK_CUR (1)
                EnumVariantInfo(name="End", associated_types=()),       # SEEK_END (2)
            )
        )
        self.enums.by_name["SeekFrom"] = seek_from_enum
        self.enums.order.append("SeekFrom")
        self.known_types.add(seek_from_enum)

        # FileError enum - file error types
        # Maps errno values to user-friendly error variants
        file_error_enum = EnumType(
            name="FileError",
            variants=(
                EnumVariantInfo(name="NotFound", associated_types=()),          # ENOENT - File does not exist
                EnumVariantInfo(name="PermissionDenied", associated_types=()),  # EACCES, EPERM - Insufficient permissions
                EnumVariantInfo(name="AlreadyExists", associated_types=()),     # EEXIST - File already exists
                EnumVariantInfo(name="IsDirectory", associated_types=()),       # EISDIR - Path refers to a directory
                EnumVariantInfo(name="DiskFull", associated_types=()),          # ENOSPC - No space left on device
                EnumVariantInfo(name="TooManyOpen", associated_types=()),       # EMFILE, ENFILE - Too many open files
                EnumVariantInfo(name="InvalidPath", associated_types=()),       # ENAMETOOLONG - Invalid path or filename
                EnumVariantInfo(name="IOError", associated_types=()),           # EIO - Generic I/O error
                EnumVariantInfo(name="Other", associated_types=()),             # Any other error
            )
        )
        self.enums.by_name["FileError"] = file_error_enum
        self.enums.order.append("FileError")
        self.known_types.add(file_error_enum)

        # FileResult enum - Result type for open() function
        # Variant: Ok(file) - success with file handle
        # Variant: Err(FileError) - failure with error information
        # Note: Uses Ok/Err naming (not Success/Error) to be consistent with Result<T>
        # No token conflict because enum variants are always qualified (FileResult.Ok vs Result.Ok)
        file_result_enum = EnumType(
            name="FileResult",
            variants=(
                EnumVariantInfo(name="Ok", associated_types=(BuiltinType.FILE,)),   # Success with file handle
                EnumVariantInfo(name="Err", associated_types=(file_error_enum,)),    # Failure with error information
            )
        )
        self.enums.by_name["FileResult"] = file_result_enum
        self.enums.order.append("FileResult")
        self.known_types.add(file_result_enum)

    def _register_predefined_generics(self) -> None:
        """Register predefined generic enums and structs.

        These generic types are built into the language and available globally:

        Generic Enums:
        - Result<T>: Generic error handling type with Ok(T) and Err() variants
        - Maybe<T>: Optional values with Some(T) and None() variants

        Generic Structs:
        - Own<T>: Unique ownership of heap-allocated data (for recursive types)

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
        # Result<T> generic enum - error handling
        # Type parameter: T (the success value type)
        # Variants:
        #   Ok(T) - success with value of type T
        #   Err() - failure with no additional data
        result_generic = GenericEnumType(
            name="Result",
            type_params=(TypeParameter(name="T"),),
            variants=(
                EnumVariantInfo(
                    name="Ok",
                    # Ok variant holds a value of type T (the generic parameter)
                    # We use TypeParameter("T") to represent the generic type
                    associated_types=(TypeParameter(name="T"),)
                ),
                EnumVariantInfo(
                    name="Err",
                    # Err variant has no associated data
                    associated_types=()
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
        from semantics.generics.types import GenericStructType
        from semantics.typesys import PointerType
        own_generic = GenericStructType(
            name="Own",
            type_params=(TypeParameter(name="T"),),
            # Field stores a pointer to T (T*)
            fields=(("value", PointerType(pointee_type=TypeParameter(name="T"))),)
        )
        self.generic_structs.by_name["Own"] = own_generic
        self.generic_structs.order.append("Own")

        # HashMap<K, V> generic struct - hash table with open addressing
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
        from semantics.typesys import PointerType

        # For now, we define HashMap with abstract fields
        # The actual Entry<K, V>[] structure will be handled during LLVM emission
        # We use i32[] as a placeholder for buckets - the real LLVM type will be Entry<K,V>[]
        from semantics.typesys import DynamicArrayType
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

    def _register_stdlib_functions(self, root: Program) -> None:
        """
        Register stdlib functions from imported modules into the function table.

        This method extracts `use <module>` statements from the AST and registers
        all functions from those stdlib modules in the function table. This allows
        type validation and code generation to query stdlib function metadata
        uniformly through the function table.

        Args:
            root: Program AST with use statements
        """
        from semantics.stdlib_registry import get_stdlib_registry

        # Get the global stdlib registry
        registry = get_stdlib_registry()

        # Extract stdlib imports from use statements
        uses = getattr(root, "uses", None)
        if not isinstance(uses, list):
            return

        for use_stmt in uses:
            if not use_stmt.is_stdlib:
                continue  # Skip user modules

            module_path = use_stmt.path

            # Get the module from registry
            module = registry.get_module(module_path)
            if module is None:
                # Module not found in registry - might be io/stdio, io/files, etc.
                # For now, only time, math, and sys/env are in the registry
                continue

            # Register all functions from this module
            for func_name, stdlib_func in module.functions.items():
                self.funcs.register_stdlib_function(module_path, stdlib_func)

            # Register all constants from this module (e.g., PI, E, TAU)
            for const_name, stdlib_const in module.constants.items():
                self.funcs.register_stdlib_function(module_path, stdlib_const)
