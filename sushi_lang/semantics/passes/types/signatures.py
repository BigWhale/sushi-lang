"""Declaration signature validation for type validation (the typecheck pass)."""
from __future__ import annotations

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import FuncDef, ExtendDef, ExtendWithDef
from sushi_lang.semantics.typesys import (
    BuiltinType, UnknownType, ArrayType, DynamicArrayType, StructType, EnumType
)
from sushi_lang.semantics.type_resolution import resolve_unknown_type

from .utils import validate_type_name, validate_and_register_parameters
from .perks import validate_perk_implementation, check_no_conflicts_with_regular_methods
from sushi_lang.semantics.generics.type_display import display_type


def _check_public_fn_ptr_fence(self, func: FuncDef) -> None:
    """CE5008: a `public fn` may not expose a foreign `ptr` in its signature."""
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
    self.extension_return_type = None
    self.variable_types = {}  # Reset for each function
    self.destroyed_arrays = [set()]  # Reset for each function with initial scope

    validate_and_register_parameters(self, func.params)

    validate_type_name(self, func.ret, func.ret_span)

    if func.err_type is not None:
        validate_type_name(self, func.err_type, func.ret_span)  # Use ret_span since we don't have err_span

        resolved_err_type = func.err_type

        if isinstance(func.err_type, UnknownType):
            resolved_err_type = resolve_unknown_type(
                func.err_type,
                self.struct_table.by_name,
                self.enum_table.by_name
            )

        if not isinstance(resolved_err_type, EnumType):
            self.err.emit(er.ERR.CE2084, func.ret_span,
                         type_name=display_type(func.err_type))

    self._validate_block(func.body)

    if func.ret != BuiltinType.BLANK:
        if not self._block_always_returns(func.body):
            self.err.emit(er.ERR.CE0107, func.name_span, name=func.name)

    self.current_function = None


def _self_registration_type(target_type, self_mode):
    """The type `self` registers under: the bare target, or its reference (#327)."""
    if self_mode is None:
        return target_type
    from sushi_lang.semantics.typesys import BorrowMode, ReferenceType
    mode = BorrowMode.POKE if self_mode == "poke" else BorrowMode.PEEK
    return ReferenceType(target_type, mode)


def validate_extension_method(self, ext: ExtendDef) -> None:
    """Validate types within an extension method."""
    self.current_function = None  # Extension methods are not functions, but we can reuse some logic
    self.in_extension_context = True  # Dedicated flag: this body returns a bare value
    self.extension_method_name = ext.name
    self.extension_return_type = ext.ret  # Bare return type; validate_return_statement checks against it
    self.variable_types = {}  # Reset for each extension method
    self.destroyed_arrays = [set()]  # Reset for each extension method with initial scope

    validate_type_name(self, ext.target_type, ext.target_type_span)

    # Blank type cannot be used as target type for extension methods
    if ext.target_type == BuiltinType.BLANK:
        self.err.emit(er.ERR.CE2032, ext.target_type_span)

    # Add 'self' parameter with target type to variable table. A `poke self` /
    # `peek self` receiver (#327) registers its full ReferenceType, so every consumer
    # that asks "is this name a borrow?" answers truthfully and inference auto-derefs.
    self_type = None
    if isinstance(ext.target_type, (BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType)):
        self_type = ext.target_type
    elif isinstance(ext.target_type, UnknownType):
        resolved_type = resolve_unknown_type(ext.target_type, self.struct_table.by_name, self.enum_table.by_name)
        if resolved_type != ext.target_type:
            self_type = resolved_type
    if self_type is not None:
        self.variable_types["self"] = _self_registration_type(self_type, getattr(ext, "self_mode", None))

    validate_and_register_parameters(self, ext.params)

    validate_type_name(self, ext.ret, ext.ret_span)

    self._validate_block(ext.body)

    if ext.ret != BuiltinType.BLANK:
        if not self._block_always_returns(ext.body):
            self.err.emit(er.ERR.CE0107, ext.name_span, name=ext.name)

    self.in_extension_context = False
    self.extension_method_name = None


def validate_perk_implementation_method(self, impl: ExtendWithDef) -> None:
    """Validate a perk implementation."""
    perk_def = self.perk_table.by_name.get(impl.perk_name)
    if not perk_def:
        self.err.emit(er.ERR.CE4003, impl.perk_name_span, perk=impl.perk_name)
        return

    validate_perk_implementation(impl, perk_def, self.reporter)

    resolved_type = impl.target_type
    if isinstance(impl.target_type, UnknownType):
        resolved_type = resolve_unknown_type(impl.target_type, self.struct_table.by_name, self.enum_table.by_name)
    if resolved_type is not None:
        check_no_conflicts_with_regular_methods(resolved_type, impl, self.extension_table, self.reporter)

    for method in impl.methods:
        self.current_function = None
        self.in_extension_context = True  # Dedicated flag: this body returns a bare value
        self.extension_method_name = method.name
        self.extension_return_type = method.ret  # Bare return type; checked in validate_return_statement
        self.variable_types = {}
        self.destroyed_arrays = [set()]

        validate_type_name(self, impl.target_type, impl.target_type_span)

        # Add 'self' parameter with target type (ReferenceType for `poke self`, #327)
        self_type = None
        if isinstance(impl.target_type, (BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType)):
            self_type = impl.target_type
        elif isinstance(impl.target_type, UnknownType):
            resolved_type = resolve_unknown_type(impl.target_type, self.struct_table.by_name, self.enum_table.by_name)
            if resolved_type != impl.target_type:
                self_type = resolved_type
        if self_type is not None:
            self.variable_types["self"] = _self_registration_type(self_type, getattr(method, "self_mode", None))

        validate_and_register_parameters(self, method.params)

        validate_type_name(self, method.ret, method.ret_span)

        self._validate_block(method.body)

        if method.ret != BuiltinType.BLANK:
            if not self._block_always_returns(method.body):
                self.err.emit(er.ERR.CE0107, method.name_span, name=method.name)

        self.in_extension_context = False
        self.extension_method_name = None
