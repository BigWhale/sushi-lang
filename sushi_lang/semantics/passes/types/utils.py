"""Shared utilities for type validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional

from sushi_lang.internals.report import Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import Type, BuiltinType, UnknownType, ArrayType, DynamicArrayType, StructType, EnumType, ReferenceType
from sushi_lang.semantics.type_resolution import resolve_unknown_type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Param, Expr
    from . import TypeValidator


def validate_type_name(validator: 'TypeValidator', type_obj: Optional[Type], span: Optional[Span]) -> None:
    """Validate that a type name is known/valid."""
    if type_obj is None:
        return

    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(type_obj, GenericTypeRef):
        # CE2419: a reference as a generic type argument. Checked FIRST, above the Result
        # branch, and with NO Maybe/Result exemption -- unlike the `ptr` gate below, whose
        # exemption is safe because a `ptr` has no lifetime to outlive. `Maybe@(peek T)`
        # and `Result@(peek T, E)` are precisely how a returned borrow escapes into a
        # `match` (#314/#316), so exempting them would leave the hole CE2417 closes.
        # This also pre-empts the backend's CE0022 on `List@(peek T)` (#318): Pass 2 runs
        # first, so the ICE is now unreachable.
        from sushi_lang.semantics.type_predicates import contains_reference
        if any(contains_reference(arg) for arg in type_obj.type_args):
            offender = next(arg for arg in type_obj.type_args if contains_reference(arg))
            er.emit(validator.reporter, er.ERR.CE2419, span, ty=display_type(offender))
            return

        if type_obj.base_name == "Result" and len(type_obj.type_args) == 2:
            for type_arg in type_obj.type_args:
                validate_type_name(validator, type_arg, span)
            return

        # CE5012: foreign `ptr` as a generic type argument is only supported by
        # the built-in Result/Maybe enum machinery. Other containers (HashMap,
        # List, user generics) cannot carry an opaque handle - check BEFORE the
        # existence checks so the user sees the real reason, not a missing
        # monomorphization (CE2001).
        if type_obj.base_name != "Maybe":
            from sushi_lang.semantics.type_predicates import contains_foreign_ptr
            if any(contains_foreign_ptr(arg) for arg in type_obj.type_args):
                er.emit(validator.reporter, er.ERR.CE5012, span, base=type_obj.base_name)
                return

        is_generic_enum = type_obj.base_name in validator.generic_enum_table.by_name
        is_generic_struct = type_obj.base_name in validator.generic_struct_table.by_name

        if not is_generic_enum and not is_generic_struct:
            er.emit(validator.reporter, er.ERR.CE2001, span, name=type_obj.base_name)
            return

        for type_arg in type_obj.type_args:
            validate_type_name(validator, type_arg, span)

        type_args_str = ", ".join(str(arg) for arg in type_obj.type_args)
        concrete_name = f"{type_obj.base_name}<{type_args_str}>"

        if concrete_name not in validator.enum_table.by_name and concrete_name not in validator.struct_table.by_name:
            # Monomorphized type should exist after monomorphization pass
            # If not, it means this instantiation wasn't collected. `concrete_name`
            # stays `<>` (it is the table lookup key above); the user sees `@()`.
            er.emit(validator.reporter, er.ERR.CE2001, span, name=display_type(type_obj))
        return

    if isinstance(type_obj, UnknownType):
        if type_obj.name in validator.struct_table.by_name:
            return
        if type_obj.name in validator.enum_table.by_name:
            return
        er.emit(validator.reporter, er.ERR.CE2001, span, name=display_type(type_obj))
    elif isinstance(type_obj, BuiltinType) and type_obj not in validator.known_types:
        er.emit(validator.reporter, er.ERR.CE2001, span, name=display_type(type_obj))
    elif isinstance(type_obj, ArrayType):
        # Blank type cannot be used as array base type
        if type_obj.base_type == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2032, span)
            return
        validate_type_name(validator, type_obj.base_type, span)
        # Validate array size (CE2010: Array size must be positive integer literal)
        if type_obj.size <= 0:
            er.emit(validator.reporter, er.ERR.CE2010, span, size=type_obj.size)
    elif isinstance(type_obj, DynamicArrayType):
        # Blank type cannot be used as dynamic array base type
        if type_obj.base_type == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2032, span)
            return
        validate_type_name(validator, type_obj.base_type, span)


def resolve_declared_type(validator: 'TypeValidator', ty: Optional[Type]) -> Optional[Type]:
    """The concrete type that a DECLARED type names."""
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.typesys import FunctionType

    if isinstance(ty, UnknownType):
        return resolve_unknown_type(ty, validator.struct_table.by_name,
                                    validator.enum_table.by_name)
    if isinstance(ty, GenericTypeRef):
        interned = str(ty)
        return (validator.enum_table.by_name.get(interned)
                or validator.struct_table.by_name.get(interned)
                or ty)
    if isinstance(ty, (FunctionType, ArrayType, DynamicArrayType)):
        from sushi_lang.semantics.type_resolution import resolve_type_recursively
        return resolve_type_recursively(ty, validator.struct_table.by_name,
                                        validator.enum_table.by_name)
    return ty


def validate_and_register_parameters(validator: 'TypeValidator', params: List['Param']) -> None:
    """Validate parameter types and register them in the variable_types table."""
    for param in params:
        validate_type_name(validator, param.ty, param.type_span)

        # Blank type cannot be used for parameters
        if param.ty == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2032, param.type_span)
            continue

        if isinstance(param.ty, ReferenceType):
            # The REFERENT gets the same resolution as a by-value parameter of that type
            # (#305). A borrow of a type is not a different type.
            resolved_ref = ReferenceType(
                referenced_type=resolve_declared_type(validator, param.ty.referenced_type),
                mutability=param.ty.mutability
            )
            validator.variable_types[param.name] = resolved_ref
            continue

        from sushi_lang.semantics.typesys import FunctionType
        if isinstance(param.ty, (FunctionType, ArrayType, DynamicArrayType)):
            # A container spelling: the WRAPPER is concrete but its members may not be.
            # A function type binds its implicit UnknownType("StdError"); an array binds
            # its element, which `P[]` leaves unresolved (#284).
            validator.variable_types[param.name] = resolve_declared_type(validator, param.ty)
            continue

        if isinstance(param.ty, (BuiltinType, StructType, EnumType)):
            validator.variable_types[param.name] = param.ty
        elif isinstance(param.ty, UnknownType):
            resolved_type = resolve_declared_type(validator, param.ty)
            if resolved_type != param.ty:
                validator.variable_types[param.name] = resolved_type
        else:
            from sushi_lang.semantics.generics.types import GenericTypeRef
            if isinstance(param.ty, GenericTypeRef):
                resolved_type = resolve_declared_type(validator, param.ty)
                if isinstance(resolved_type, GenericTypeRef):
                    resolved_type = None

                if resolved_type is not None:
                    validator.variable_types[param.name] = resolved_type
                    param.ty = resolved_type  # Update AST node for backend

                    if validator.current_function and validator.current_function.name in validator.func_table.by_name:
                        func_sig = validator.func_table.by_name[validator.current_function.name]
                        for sig_param in func_sig.params:
                            if sig_param.name == param.name:
                                sig_param.ty = resolved_type
                                break
                else:
                    pass


def reject_spread_args(validator: 'TypeValidator', args: List) -> bool:
    """Reject any bloom spread `arr...` argument in a context that is never variadic."""
    from sushi_lang.semantics.ast import Spread
    found = False
    for arg in args:
        if isinstance(arg, Spread):
            er.emit(validator.reporter, er.ERR.CE0120, arg.loc,
                    message="bloom argument 'arr...' is only allowed as the last argument "
                            "of a call to a variadic '...T' function")
            validator.validate_expression(arg)
            found = True
    return found


def mark_array_destroyed(validator: 'TypeValidator', name: str) -> None:
    """Mark a dynamic array as destroyed in the current scope."""
    if validator.destroyed_arrays:
        validator.destroyed_arrays[-1].add(name)


def is_array_destroyed(validator: 'TypeValidator', name: str) -> bool:
    """Check if a dynamic array has been destroyed in any current scope."""
    for destroyed_set in validator.destroyed_arrays:
        if name in destroyed_set:
            return True
    return False


def push_destroyed_scope(validator: 'TypeValidator') -> None:
    """Push a new scope for tracking destroyed arrays."""
    validator.destroyed_arrays.append(set())


def pop_destroyed_scope(validator: 'TypeValidator') -> None:
    """Pop the current scope for tracking destroyed arrays."""
    if validator.destroyed_arrays:
        validator.destroyed_arrays.pop()


def propagate_enum_type_to_dotcall(
    validator: 'TypeValidator',
    arg: 'Expr',
    expected_type: Optional[Type]
) -> None:
    """Propagate expected enum type to DotCall nodes for generic enums."""
    if expected_type is None:
        return

    from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
    propagate_types_to_value(validator, arg, expected_type)


def propagate_struct_type_to_dotcall(
    validator: 'TypeValidator',
    arg: 'Expr',
    expected_type: Optional[Type]
) -> None:
    """Propagate expected struct type to DotCall nodes for generic structs."""
    if expected_type is None:
        return

    from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
    propagate_types_to_value(validator, arg, expected_type)
