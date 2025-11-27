# semantics/passes/collect/functions.py
"""Function and extension method collection for Phase 0."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from internals.report import Reporter, Span
from internals import errors as er
from internals.errors import ERR
from semantics.ast import (
    Program,
    FuncDef,
    ExtendDef,
    BoundedTypeParam,
    Block,
)
from semantics.typesys import (
    Type,
    BuiltinType,
    UnknownType,
    ArrayType,
    StructType,
    EnumType,
    ResultType,
)
from semantics.generics.types import (
    TypeParameter,
    GenericTypeRef,
    TypeParam,
)

from .utils import extract_type_param_names, format_location, param_from_node


def is_explicit_result_type(ty: Optional[Type]) -> bool:
    """Check if a type is an explicit Result<T, E>.

    Returns True if:
    - Type is ResultType (from semantic analysis)
    - Type is GenericTypeRef with base_name "Result"

    This is used to detect when a function return type is already wrapped
    in Result, so we don't double-wrap it.
    """
    if ty is None:
        return False
    if isinstance(ty, ResultType):
        return True
    if isinstance(ty, GenericTypeRef) and ty.base_name == "Result":
        return True
    return False


@dataclass
class Param:
    """Function parameter with type information."""
    name: str
    ty: Optional[Type]
    name_span: Optional[Span]
    type_span: Optional[Span]
    index: int


@dataclass
class FuncSig:
    """Phase 0 function signature.

    Types are Optional to allow defensive collection before full typing.
    """
    name: str
    loc: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_type: Optional[Type] = None
    ret_span: Optional[Span] = None
    params: List[Param] = field(default_factory=list)
    is_public: bool = False              # True if declared with 'public' keyword
    unit_name: Optional[str] = None      # Which unit this function belongs to (for multi-file)
    err_type: Optional[Type] = None      # Error type for Result<T, E> (None = StdError default)


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
    err_type: Optional[Type] = None              # Error type for Result<T, E> (None = StdError default)


@dataclass
class FunctionTable:
    """Table of function signatures collected in Phase 0."""
    by_name: Dict[str, FuncSig] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    # Stdlib functions: (module_path, function_name) -> StdlibFunction
    _stdlib_functions: Dict[Tuple[str, str], Any] = field(default_factory=dict)

    def register_stdlib_function(self, module_path: str, stdlib_func: Any) -> None:
        """Register a stdlib function.

        Args:
            module_path: Module path (e.g., "time", "sys/env")
            stdlib_func: StdlibFunction metadata from stdlib_registry
        """
        key = (module_path, stdlib_func.name)
        self._stdlib_functions[key] = stdlib_func

    def lookup_stdlib_function(self, module_path: str, function_name: str) -> Optional[Any]:
        """Lookup a stdlib function by module and name.

        Args:
            module_path: Module path (e.g., "time", "sys/env")
            function_name: Function name (e.g., "sleep", "getenv")

        Returns:
            StdlibFunction metadata or None if not found
        """
        return self._stdlib_functions.get((module_path, function_name))

    def is_stdlib_function(self, module_path: str, function_name: str) -> bool:
        """Check if a function is a stdlib function.

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
class ExtensionMethod:
    """Phase 0 extension method signature.

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
    """Table of extension methods organized by target type.

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
    """Phase 0 generic extension method signature.

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
class GenericExtensionTable:
    """Table of generic extension methods organized by base type name.

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


class FunctionCollector:
    """Collector for function and extension method definitions.

    Collects:
    - Regular functions (concrete)
    - Generic functions
    - Extension methods (regular)
    - Generic extension methods
    - Stdlib function registrations

    Validates:
    - No duplicate names (across all function tables)
    - Parameter uniqueness
    - main() return type is integer
    """

    def __init__(
        self,
        reporter: Reporter,
        funcs: FunctionTable,
        generic_funcs: GenericFunctionTable,
        extensions: ExtensionTable,
        generic_extensions: GenericExtensionTable,
        structs: 'StructTable',
        enums: 'EnumTable',
        generic_structs: 'GenericStructTable',
        generic_enums: 'GenericEnumTable'
    ) -> None:
        """Initialize function collector.

        Args:
            reporter: Error reporter
            funcs: Shared function table
            generic_funcs: Shared generic function table
            extensions: Shared extension method table
            generic_extensions: Shared generic extension table
            structs: Regular struct table (for type resolution)
            enums: Regular enum table (for type resolution)
            generic_structs: Generic struct table (for validation)
            generic_enums: Generic enum table (for validation)
        """
        self.r = reporter
        self.funcs = funcs
        self.generic_funcs = generic_funcs
        self.extensions = extensions
        self.generic_extensions = generic_extensions
        self.structs = structs
        self.enums = enums
        self.generic_structs = generic_structs
        self.generic_enums = generic_enums

    def collect_functions(self, root: Program, unit_name: Optional[str] = None) -> None:
        """Collect all function definitions from program AST.

        Args:
            root: Program AST node
            unit_name: Optional unit name for multi-file compilation
        """
        funcs = getattr(root, "functions", None)
        if isinstance(funcs, list):
            for fn in funcs:
                if isinstance(fn, FuncDef):
                    self._collect_function_def(fn, unit_name=unit_name)

    def collect_extensions(self, root: Program) -> None:
        """Collect all extension method definitions from program AST.

        Args:
            root: Program AST node
        """
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

    def register_stdlib_functions(self, root: Program) -> None:
        """Register stdlib functions from imported modules into the function table.

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

    def _collect_function_def(self, fn: FuncDef, unit_name: Optional[str] = None) -> None:
        """Dispatch function collection based on whether it's generic.

        Args:
            fn: Function definition AST node
            unit_name: Optional unit name for multi-file compilation
        """
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
        """Collect concrete (non-generic) function definition.

        Args:
            fn: Function definition AST node
            unit_name: Optional unit name for multi-file compilation
        """
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

        # Check for mixing explicit Result<T, E> with | ErrorType syntax
        err_ty: Optional[Type] = getattr(fn, "err_type", None)
        if is_explicit_result_type(ret_ty) and err_ty is not None:
            # User wrote: fn foo() Result<T, E1> | E2
            # This is an error because it's ambiguous and implies nesting
            err_type_name = getattr(err_ty, "name", str(err_ty))
            er.emit(self.r, ERR.CE2085, ret_span, err_type=err_type_name)

        params: List[Param] = []
        param_names: Set[str] = set()
        for idx, p in enumerate(getattr(fn, "params", []) or []):
            param = param_from_node(p, idx)

            # Check for duplicate parameter names
            if param.name in param_names:
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            else:
                param_names.add(param.name)

            params.append(param)

        # Check for duplicates in ALL function tables
        if name in self.funcs.by_name:
            prev = self.funcs.by_name[name]
            prev_loc = format_location(self.r, prev.name_span)
            er.emit(self.r, ERR.CE0101, name_span, name=name, prev_loc=prev_loc)
            return

        if name in self.generic_funcs.by_name:
            prev = self.generic_funcs.by_name[name]
            prev_loc = format_location(self.r, prev.name_span)
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
            err_type=fn.err_type,
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

        Args:
            fn: Function definition AST node
            type_params_raw: Raw type parameters from AST
            unit_name: Optional unit name for multi-file compilation
        """
        name = fn.name
        name_span = getattr(fn, "name_span", None) or getattr(fn, "loc", None)

        # Check for duplicates in ALL function tables
        if name in self.generic_funcs.by_name:
            prev = self.generic_funcs.by_name[name]
            prev_loc = format_location(self.r, prev.name_span)
            er.emit(self.r, ERR.CE0101, name_span, name=name, prev_loc=prev_loc)
            return

        if name in self.funcs.by_name:
            prev = self.funcs.by_name[name]
            prev_loc = format_location(self.r, prev.name_span)
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
            param = param_from_node(p, idx)

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

        # Check for mixing explicit Result<T, E> with | ErrorType syntax
        err_ty = getattr(fn, "err_type", None)
        if is_explicit_result_type(ret_ty) and err_ty is not None:
            # User wrote: fn foo<T>() Result<T, E1> | E2
            # This is an error because it's ambiguous and implies nesting
            err_type_name = getattr(err_ty, "name", str(err_ty))
            er.emit(self.r, ERR.CE2085, ret_span, err_type=err_type_name)

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
            ret_span=ret_span,
            err_type=fn.err_type
        )

        # Store in generic function table
        self.generic_funcs.order.append(name)
        self.generic_funcs.by_name[name] = generic_func

    def _collect_extension_def(self, ext: ExtendDef) -> None:
        """Collect extension method definition (both regular and generic).

        Args:
            ext: Extension definition AST node
        """
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
        param_names: Set[str] = set()
        for idx, p in enumerate(getattr(ext, "params", []) or []):
            param = param_from_node(p, idx)

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
                prev_loc = format_location(self.r, existing.name_span)
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
                    prev_loc = format_location(self.r, existing.name_span)
                    er.emit(self.r, ERR.CE0101, name_span,
                           name=f"extension method '{name}' for '{resolved_type}'",
                           prev_loc=prev_loc)
                    return

            # Add method to table (skip duplicate checking for unknown types)
            if resolved_type is not None and isinstance(resolved_type, (BuiltinType, ArrayType, StructType, EnumType)):
                self.extensions.add_method(method)
