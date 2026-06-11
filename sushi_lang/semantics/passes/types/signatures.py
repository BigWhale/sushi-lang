# semantics/passes/types/signatures.py
"""
Declaration signature validation for type validation (Pass 2).

Validates the signature and body of the three declaration kinds that share
return-type / parameter / return-reachability machinery:
- regular functions
- extension methods
- perk implementation methods

Functions receive the TypeValidator instance (``self``) and mutate it in
place (current_function, variable_types, destroyed_arrays), matching the
delegation pattern used across this package.
"""
from __future__ import annotations

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import FuncDef, ExtendDef, ExtendWithDef
from sushi_lang.semantics.typesys import (
    BuiltinType, UnknownType, ArrayType, DynamicArrayType, StructType, EnumType
)
from sushi_lang.semantics.type_resolution import resolve_unknown_type

from .utils import validate_type_name, validate_and_register_parameters
from .perks import validate_perk_implementation, check_no_conflicts_with_regular_methods


def _check_public_fn_ptr_fence(self, func: FuncDef) -> None:
    """CE5008: a `public fn` may not expose a foreign `ptr` in its signature.

    FFI handles are a private unit detail. The fence fires whenever the
    `public` keyword is present, even in single-file programs (where `public`
    is otherwise a no-op) - simpler, and keeps the rule learnable before a
    program grows a second unit. Struct fields may still carry `ptr` across
    units (the wrapper-struct pattern); only the function signature itself
    is checked. Extensions and perk methods cannot be `public` (grammar).
    """
    from sushi_lang.semantics.type_predicates import contains_foreign_ptr

    if not func.is_public:
        return
    structs = self.struct_table.by_name
    enums = self.enum_table.by_name
    if contains_foreign_ptr(func.ret, structs, enums) or any(
        contains_foreign_ptr(p.ty, structs, enums) for p in func.params
    ):
        self.err.emit(er.ERR.CE5008, func.name_span, name=func.name)


def validate_function(self, func: FuncDef) -> None:
    """Validate types within a function."""
    self.current_function = func
    _check_public_fn_ptr_fence(self, func)
    self.in_extension_context = False  # Normal functions are never extension/perk bodies
    self.extension_method_name = None
    self.variable_types = {}  # Reset for each function
    self.destroyed_arrays = [set()]  # Reset for each function with initial scope

    # Validate parameter types and add them to variable table
    validate_and_register_parameters(self, func.params)

    # Validate return type (blank type is allowed here)
    validate_type_name(self, func.ret, func.ret_span)

    # Validate error type if specified (must be an enum)
    if func.err_type is not None:
        # First validate the type name itself
        validate_type_name(self, func.err_type, func.ret_span)  # Use ret_span since we don't have err_span

        # Then check if it's an enum
        resolved_err_type = func.err_type

        # Resolve UnknownType to actual type
        if isinstance(func.err_type, UnknownType):
            resolved_err_type = resolve_unknown_type(
                func.err_type,
                self.struct_table.by_name,
                self.enum_table.by_name
            )

        # Check if resolved type is an enum
        if not isinstance(resolved_err_type, EnumType):
            # Error type must be an enum, not a struct or primitive
            self.err.emit(er.ERR.CE2084, func.ret_span,
                         type_name=str(func.err_type))

    # Validate function body
    self._validate_block(func.body)

    # Check if function returns a value on all code paths
    # Skip this check for functions returning blank (~)
    if func.ret != BuiltinType.BLANK:
        if not self._block_always_returns(func.body):
            self.err.emit(er.ERR.CE0107, func.name_span, name=func.name)

    self.current_function = None


def validate_extension_method(self, ext: ExtendDef) -> None:
    """Validate types within an extension method."""
    self.current_function = None  # Extension methods are not functions, but we can reuse some logic
    self.in_extension_context = True  # Dedicated flag: this body returns a bare value
    self.extension_method_name = ext.name
    self.variable_types = {}  # Reset for each extension method
    self.destroyed_arrays = [set()]  # Reset for each extension method with initial scope

    # Validate target type
    validate_type_name(self, ext.target_type, ext.target_type_span)

    # Blank type cannot be used as target type for extension methods
    if ext.target_type == BuiltinType.BLANK:
        self.err.emit(er.ERR.CE2032, ext.target_type_span)

    # Add 'self' parameter with target type to variable table
    if isinstance(ext.target_type, (BuiltinType, ArrayType, DynamicArrayType, StructType)):
        self.variable_types["self"] = ext.target_type
    elif isinstance(ext.target_type, UnknownType):
        # Resolve UnknownType to StructType for struct-typed self
        resolved_type = resolve_unknown_type(ext.target_type, self.struct_table.by_name, self.enum_table.by_name)
        if resolved_type != ext.target_type:
            self.variable_types["self"] = resolved_type

    # Validate explicit parameter types and add them to variable table
    validate_and_register_parameters(self, ext.params)

    # Validate return type (blank type is allowed here)
    validate_type_name(self, ext.ret, ext.ret_span)

    # Validate extension method body
    self._validate_block(ext.body)

    # Check if extension method returns a value on all code paths
    # Skip this check for methods returning blank (~)
    if ext.ret != BuiltinType.BLANK:
        if not self._block_always_returns(ext.body):
            self.err.emit(er.ERR.CE0107, ext.name_span, name=ext.name)

    self.in_extension_context = False
    self.extension_method_name = None


def validate_perk_implementation_method(self, impl: ExtendWithDef) -> None:
    """Validate a perk implementation."""
    # Look up the perk definition
    perk_def = self.perk_table.by_name.get(impl.perk_name)
    if not perk_def:
        # Error should have been caught in collection phase, but double check
        self.err.emit(er.ERR.CE4003, impl.perk_name_span, perk=impl.perk_name)
        return

    # Validate that implementation satisfies perk requirements
    validate_perk_implementation(impl, perk_def, self.reporter)

    # Check for conflicts with regular extension methods
    resolved_type = impl.target_type
    if isinstance(impl.target_type, UnknownType):
        resolved_type = resolve_unknown_type(impl.target_type, self.struct_table.by_name, self.enum_table.by_name)
    if resolved_type is not None:
        check_no_conflicts_with_regular_methods(resolved_type, impl, self.extension_table, self.reporter)

    # Validate each method in the implementation
    for method in impl.methods:
        # Treat perk implementation methods like extension methods
        self.current_function = None
        self.in_extension_context = True  # Dedicated flag: this body returns a bare value
        self.extension_method_name = method.name
        self.variable_types = {}
        self.destroyed_arrays = [set()]

        # Validate target type
        validate_type_name(self, impl.target_type, impl.target_type_span)

        # Add 'self' parameter with target type
        if isinstance(impl.target_type, (BuiltinType, ArrayType, DynamicArrayType, StructType)):
            self.variable_types["self"] = impl.target_type
        elif isinstance(impl.target_type, UnknownType):
            resolved_type = resolve_unknown_type(impl.target_type, self.struct_table.by_name, self.enum_table.by_name)
            if resolved_type != impl.target_type:
                self.variable_types["self"] = resolved_type

        # Validate method parameters
        validate_and_register_parameters(self, method.params)

        # Validate return type
        validate_type_name(self, method.ret, method.ret_span)

        # Validate method body
        self._validate_block(method.body)

        # Check if method returns on all code paths
        if method.ret != BuiltinType.BLANK:
            if not self._block_always_returns(method.body):
                self.err.emit(er.ERR.CE0107, method.name_span, name=method.name)

        self.in_extension_context = False
        self.extension_method_name = None
