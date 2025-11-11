# semantics/passes/types/calls.py
"""
Function and method call validation for type validation.

This module contains validation functions for:
- Regular function calls
- Struct constructors
- Enum constructors (including generic enums)
- Method calls (extension methods and built-in methods)
- Built-in global functions (e.g., open)
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from internals import errors as er
from semantics.typesys import BuiltinType, ArrayType, DynamicArrayType, EnumType, StructType, UnknownType
from semantics.ast import Call, MethodCall, EnumConstructor, Name
from semantics.generics.name_mangling import mangle_function_name
from .compatibility import types_compatible
from .utils import is_array_destroyed, propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall

if TYPE_CHECKING:
    from . import TypeValidator
    from semantics.enums.variants import EnumVariant


def validate_function_call(validator: 'TypeValidator', call: Call) -> None:
    """Validate function call arguments and types (CE2006, CE2008)."""
    # Check if function exists
    function_name = call.callee.id

    # Check if this is a generic function call
    if function_name in validator.generic_func_table.by_name:
        _validate_generic_function_call(validator, call, function_name)
        return

    # Check if this is a struct constructor instead of a function call
    if function_name in validator.struct_table.by_name:
        validate_struct_constructor(validator, call)
        return

    # Check for built-in global functions
    if function_name == "open":
        validate_open_function(validator, call)
        return

    # Check if this is a stdlib function call
    # Stdlib functions are registered during Pass 0 in FunctionTable
    stdlib_func = _check_stdlib_function(validator, call)
    if stdlib_func is not None:
        # Stdlib function found - validate using its registered validator
        _validate_stdlib_function(validator, call, stdlib_func)
        return

    if function_name not in validator.func_table.by_name:
        er.emit(validator.reporter, er.ERR.CE2008, call.callee.loc, name=function_name)
        # Check if this is a generic struct constructor used inline - provide a helpful hint
        if function_name in validator.generic_struct_table.by_name:
            import sys
            print(f"      Generic struct constructors require explicit type parameters in variable declarations", file=sys.stderr)
        return

    # Get function signature
    func_sig = validator.func_table.by_name[function_name]

    # Check function visibility for cross-unit calls (multi-file compilation only)
    if (validator.current_unit_name is not None and
        func_sig.unit_name is not None and
        func_sig.unit_name != validator.current_unit_name):
        # This is a cross-unit function call - check if function is public
        if not func_sig.is_public:
            er.emit(validator.reporter, er.ERR.CE3005, call.callee.loc,
                   name=function_name,
                   current_unit=validator.current_unit_name,
                   func_unit=func_sig.unit_name)
            return

    expected_params = func_sig.params
    actual_args = call.args

    # Check argument count
    if len(actual_args) != len(expected_params):
        er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
               name=function_name, expected=len(expected_params), got=len(actual_args))
        # Still continue with validation of provided arguments

    # Validate each argument type against corresponding parameter type
    for i, (arg, param) in enumerate(zip(actual_args, expected_params)):
        # Propagate expected types to DotCall nodes for generic enums (before validation)
        # This allows Maybe.None(), Result.Ok(), etc. to work as function arguments
        propagate_enum_type_to_dotcall(validator, arg, param.ty)

        # Propagate expected types to DotCall nodes for generic structs (before validation)
        # This allows Own.alloc(42) to work as function arguments
        propagate_struct_type_to_dotcall(validator, arg, param.ty)

        # Propagate expected types to Call nodes for generic struct constructors
        # This allows Box(42) to work when parameter expects Box<i32>
        if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(param.ty, StructType):
            struct_name = arg.callee.id
            # Check if this is a generic struct constructor
            if struct_name in validator.generic_struct_table.by_name:
                # Update the Call node's callee id to use the concrete type name
                arg.callee.id = param.ty.name

        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Check type compatibility
        if param.ty is not None:  # Skip if parameter has unknown type
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and not types_compatible(validator, arg_type, param.ty):
                er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=str(param.ty), got=str(arg_type))

    # Validate any excess arguments (if more args than params)
    for i in range(len(expected_params), len(actual_args)):
        validator.validate_expression(actual_args[i])


def validate_struct_constructor(validator: 'TypeValidator', call: Call) -> None:
    """Validate struct constructor call - field count and types.

    Handles both regular structs and generic structs (e.g., Box<i32>).
    """
    struct_name = call.callee.id

    # Check if struct exists in the struct table
    if struct_name not in validator.struct_table.by_name:
        # Check if this might be a generic struct that hasn't been instantiated yet
        from semantics.generics.types import GenericTypeRef
        # For now, just emit an error - the struct should exist after monomorphization
        er.emit(validator.reporter, er.ERR.CE2001, call.callee.loc, name=struct_name)
        # Still validate arguments to propagate type information
        for arg in call.args:
            validator.validate_expression(arg)
        return

    struct_type = validator.struct_table.by_name[struct_name]

    # Get expected fields
    expected_fields = list(struct_type.fields)
    actual_args = call.args

    # Check field count
    if len(actual_args) != len(expected_fields):
        er.emit(validator.reporter, er.ERR.CE2027, call.callee.loc,
               name=struct_name, expected=len(expected_fields), got=len(actual_args))
        # Still continue with validation of provided arguments

    # Validate each argument type against corresponding field type
    for i, (arg, (field_name, field_type)) in enumerate(zip(actual_args, expected_fields)):
        # Resolve GenericTypeRef to concrete type if needed
        from semantics.generics.types import GenericTypeRef
        from semantics.typesys import StructType as StructTypeClass
        resolved_field_type = field_type
        if isinstance(field_type, GenericTypeRef):
            # Generate the concrete type name (e.g., "Maybe<i32>")
            type_args_str = ", ".join(str(arg_type) for arg_type in field_type.type_args)
            concrete_name = f"{field_type.base_name}<{type_args_str}>"

            # Look up the monomorphized concrete type
            if concrete_name in validator.struct_table.by_name:
                resolved_field_type = validator.struct_table.by_name[concrete_name]
            elif concrete_name in validator.enum_table.by_name:
                resolved_field_type = validator.enum_table.by_name[concrete_name]
            # If not found, keep the GenericTypeRef (will be caught by type compatibility check)

        # Propagate expected type to DotCall nodes for generic enums
        # This allows Point(x: Maybe.None(), y: Result.Ok(42)) for struct fields
        propagate_enum_type_to_dotcall(validator, arg, resolved_field_type)

        # Propagate expected type to DotCall nodes for generic structs
        # This allows Point(x: Own.alloc(42)) for struct fields
        propagate_struct_type_to_dotcall(validator, arg, resolved_field_type)

        # Propagate expected type to Call nodes for generic struct constructors
        # This allows Box(Box(100)) to work for nested generic structs
        if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(resolved_field_type, StructTypeClass):
            arg_struct_name = arg.callee.id
            # Check if this is a generic struct constructor
            if arg_struct_name in validator.generic_struct_table.by_name:
                # Update the Call node's callee id to use the concrete type name
                arg.callee.id = resolved_field_type.name

        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Check type compatibility
        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, resolved_field_type):
            er.emit(validator.reporter, er.ERR.CE2028, arg.loc,
                   field_name=field_name, expected=str(resolved_field_type), got=str(arg_type))

    # Validate any excess arguments (if more args than fields)
    for i in range(len(expected_fields), len(actual_args)):
        validator.validate_expression(actual_args[i])


def validate_enum_constructor(validator: 'TypeValidator', constructor: EnumConstructor) -> None:
    """Validate enum variant constructor - variant exists, argument count and types.

    This is the main orchestrator method that delegates to focused helper methods.
    """
    # Step 1: Resolve enum type (handles concrete and generic enums)
    enum_type = resolve_enum_type(validator, constructor)
    if enum_type is None:
        return

    # Step 2: Validate variant exists in the enum
    variant = validate_variant_exists(validator, enum_type, constructor)
    if variant is None:
        return

    # Step 3: Propagate generic types to nested enum constructors
    propagate_generic_types_to_nested_constructors(validator, constructor, variant)

    # Step 4: Validate constructor arguments (count and types)
    validate_constructor_arguments(validator, constructor, variant, enum_type)


def resolve_enum_type(validator: 'TypeValidator', constructor: EnumConstructor) -> Optional[EnumType]:
    """Resolve constructor to concrete or generic enum type.

    Args:
        validator: The TypeValidator instance.
        constructor: The enum constructor to resolve.

    Returns:
        The resolved EnumType if found, None otherwise.
    """
    # Priority 1: Check if resolved_enum_type is already set (for generic enums like Result<T>)
    # This is set by _validate_return_statement or _validate_let_statement
    if hasattr(constructor, 'resolved_enum_type') and constructor.resolved_enum_type is not None:
        return constructor.resolved_enum_type

    enum_name = constructor.enum_name

    # Priority 2: Check if enum exists as a concrete enum in the enum table
    if enum_name in validator.enum_table.by_name:
        return validator.enum_table.by_name[enum_name]

    # Priority 3: Check if it's a known generic enum (like Result, Maybe)
    if enum_name in validator.generic_enum_table.by_name:
        # Fallback path: Generic enum constructor without concrete type context
        # This handles edge cases where a generic enum (Result, Maybe) is used without
        # type resolution from let/return statements. In practice, resolved_enum_type
        # should be set by _validate_return_statement or _validate_let_statement before
        # validation reaches this point, allowing full type checking against the
        # monomorphized enum type (e.g., Result<i32>, Maybe<string>).
        #
        # We still validate nested arguments to ensure type propagation for cases like:
        # Result.Ok(Maybe.Some(42)) where the nested Maybe.Some needs processing.
        for arg in constructor.args:
            validator.validate_expression(arg)
        return None

    # Priority 4: Unknown enum type
    er.emit(validator.reporter, er.ERR.CE2001, constructor.enum_name_span or constructor.loc,
           name=enum_name)
    return None


def validate_variant_exists(
    validator: 'TypeValidator', enum_type: EnumType, constructor: EnumConstructor
) -> Optional['EnumVariant']:
    """Check variant exists in enum and return it.

    Args:
        validator: The TypeValidator instance.
        enum_type: The enum type to check.
        constructor: The constructor with the variant name.

    Returns:
        The EnumVariant if found, None otherwise.
    """
    variant_name = constructor.variant_name
    variant = enum_type.get_variant(variant_name)

    if variant is None:
        er.emit(validator.reporter, er.ERR.CE2045, constructor.variant_name_span or constructor.loc,
               variant=variant_name, enum=enum_type.name)
        return None

    return variant


def propagate_generic_types_to_nested_constructors(
    validator: 'TypeValidator', constructor: EnumConstructor, variant: 'EnumVariant'
) -> None:
    """Set resolved_enum_type for nested generic enum constructors.

    This propagates type information from outer constructors to nested ones,
    enabling proper type checking for cases like Maybe.Some(Result.Ok(42)).

    Args:
        validator: The TypeValidator instance.
        constructor: The outer enum constructor.
        variant: The variant being constructed.
    """
    from semantics.ast import DotCall, Name
    from semantics.generics.types import GenericTypeRef

    expected_types = list(variant.associated_types)
    actual_args = constructor.args

    # Propagate expected types to nested generic enum constructors BEFORE validation
    for i, (arg, expected_type) in enumerate(zip(actual_args, expected_types)):
        # Resolve GenericTypeRef to concrete EnumType
        # This handles cases like Maybe<Result<i32>>.Some where associated_types[0] is GenericTypeRef
        resolved_type = expected_type
        if isinstance(expected_type, GenericTypeRef):
            # Convert GenericTypeRef to concrete enum name (e.g., "Result<i32>")
            concrete_name = str(expected_type)
            if concrete_name in validator.enum_table.by_name:
                resolved_type = validator.enum_table.by_name[concrete_name]
            else:
                # Enum not monomorphized - skip propagation for this argument
                continue

        # Only propagate if we have a concrete EnumType
        if not isinstance(resolved_type, EnumType):
            continue

        # Handle both EnumConstructor and DotCall nodes
        if isinstance(arg, EnumConstructor):
            if arg.enum_name in validator.generic_enum_table.by_name:
                # Set the resolved enum type for the nested constructor
                # This must happen BEFORE validate_expression is called
                arg.resolved_enum_type = resolved_type
        elif isinstance(arg, DotCall):
            # Check if this DotCall is an enum constructor (receiver is a type name)
            if isinstance(arg.receiver, Name):
                receiver_name = arg.receiver.id
                if receiver_name in validator.generic_enum_table.by_name:
                    # Set the resolved enum type for the DotCall
                    # This allows Result.Ok(), Maybe.Some(), etc. to work as nested arguments
                    arg.resolved_enum_type = resolved_type


def validate_constructor_arguments(
    validator: 'TypeValidator', constructor: EnumConstructor, variant: 'EnumVariant', enum_type: EnumType
) -> None:
    """Validate argument count and types for enum constructor.

    Args:
        validator: The TypeValidator instance.
        constructor: The enum constructor to validate.
        variant: The variant being constructed.
        enum_type: The resolved enum type.
    """
    variant_name = constructor.variant_name
    expected_types = list(variant.associated_types)
    actual_args = constructor.args

    # Special check for Result.Ok() with zero arguments (CE2036)
    # This provides a more helpful error message than the generic "wrong argument count"
    if (enum_type.name.startswith("Result<") and variant_name == "Ok" and
        len(actual_args) == 0 and len(expected_types) == 1):
        # Check if the expected type is blank type
        expected_type = expected_types[0]
        if expected_type == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2036, constructor.loc)
            return

    # Check argument count
    if len(actual_args) != len(expected_types):
        er.emit(validator.reporter, er.ERR.CE2050, constructor.loc,
               variant=variant_name, expected=len(expected_types), got=len(actual_args))
        # Still continue with validation of provided arguments

    # Validate each argument type against corresponding associated type
    for i, (arg, expected_type) in enumerate(zip(actual_args, expected_types)):
        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Resolve UnknownType to StructType/EnumType if needed
        from semantics.typesys import UnknownType
        from semantics.type_resolution import resolve_unknown_type
        resolved_type = expected_type
        if isinstance(expected_type, UnknownType):
            resolved_type = resolve_unknown_type(expected_type, validator.struct_table.by_name, validator.enum_table.by_name)

        # Check type compatibility
        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, resolved_type):
            er.emit(validator.reporter, er.ERR.CE2049, arg.loc,
                   variant=variant_name, expected=str(resolved_type), got=str(arg_type))

    # Validate any excess arguments (if more args than expected)
    for i in range(len(expected_types), len(actual_args)):
        validator.validate_expression(actual_args[i])


def validate_open_function(validator: 'TypeValidator', call: Call) -> None:
    """Validate open() built-in function call.

    Signature: open(string path, FileMode mode) FileResult
    Returns: FileResult enum (Ok(file) or Err())
    """
    actual_args = call.args

    # Check argument count (must be exactly 2)
    if len(actual_args) != 2:
        er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
               name="open", expected=2, got=len(actual_args))
        return

    # Validate first argument: path (must be string)
    validator.validate_expression(actual_args[0])
    path_type = validator.infer_expression_type(actual_args[0])
    if path_type is not None and path_type != BuiltinType.STRING:
        er.emit(validator.reporter, er.ERR.CE2006, actual_args[0].loc,
               index=1, expected="string", got=str(path_type))

    # Validate second argument: mode (must be FileMode enum variant)
    validator.validate_expression(actual_args[1])
    mode_type = validator.infer_expression_type(actual_args[1])

    # Check if it's the FileMode enum type
    file_mode_enum = validator.enum_table.by_name.get("FileMode")
    if file_mode_enum is None:
        # FileMode enum not registered - this shouldn't happen
        return

    if mode_type is not None and mode_type != file_mode_enum:
        er.emit(validator.reporter, er.ERR.CE2006, actual_args[1].loc,
               index=2, expected="FileMode", got=str(mode_type))


def validate_method_call(validator: 'TypeValidator', call: MethodCall) -> None:
    """Validate method call - receiver type, method existence, argument types."""
    # Check for use-after-destroy (CE2024)
    if isinstance(call.receiver, Name):
        if is_array_destroyed(validator, call.receiver.id):
            er.emit(validator.reporter, er.ERR.CE2024, call.receiver.loc, name=call.receiver.id)
            return

    # First validate the receiver expression
    validator.validate_expression(call.receiver)

    # Infer receiver type
    receiver_type = validator.infer_expression_type(call.receiver)

    # Special case: static-like method calls on generic type names (e.g., List.new(), HashMap.new())
    # The receiver is a type name, not an instance, so we need to check if this is a generic struct constructor
    if receiver_type is None and isinstance(call.receiver, Name):
        type_name = call.receiver.id
        # Check if this is a List or HashMap constructor call
        if type_name == "List" and call.method in ("new", "with_capacity"):
            from backend.generics.list.validation import is_builtin_list_method
            if is_builtin_list_method(call.method):
                expected_args = {"new": 0, "with_capacity": 1}
                expected = expected_args.get(call.method, 0)
                got = len(call.args)
                if got != expected:
                    er.emit(validator.reporter, er.ERR.CE2053, call.loc,
                            method=call.method, expected=expected, got=got)
            return
        elif type_name == "HashMap" and call.method == "new":
            # HashMap.new() expects 0 arguments
            if len(call.args) != 0:
                from backend.generics.hashmap.validation import is_builtin_hashmap_method
                if is_builtin_hashmap_method(call.method):
                    er.emit(validator.reporter, er.ERR.CE2016, call.loc,
                            method=call.method, expected=0, got=len(call.args))
            return

    if receiver_type is None:
        # Can't validate method without knowing receiver type
        return

    # Check if this is a generic struct type (Own<T>, HashMap<K, V>, List<T>) that needs validation
    is_generic_struct = (isinstance(receiver_type, StructType) and
                         (receiver_type.name.startswith("Own<") or
                          receiver_type.name.startswith("HashMap<") or
                          receiver_type.name.startswith("List<")))

    # Allow StructType through for perk method checking and auto-derived methods
    if not isinstance(receiver_type, (BuiltinType, ArrayType, DynamicArrayType, EnumType, StructType)) and not is_generic_struct:
        # Can't call methods on unknown types - this is handled by unknown type validation
        return

    # Check for built-in array methods first (both fixed and dynamic arrays)
    if isinstance(receiver_type, (ArrayType, DynamicArrayType)):
        from semantics.validate_arrays import is_builtin_array_method, validate_builtin_array_method
        if is_builtin_array_method(call.method):
            validate_builtin_array_method(call, receiver_type, validator.reporter, validator)

            # Track destroy() calls on dynamic arrays
            if (call.method == "destroy" and
                isinstance(receiver_type, DynamicArrayType) and
                isinstance(call.receiver, Name)):
                from .utils import mark_array_destroyed
                mark_array_destroyed(validator, call.receiver.id)
            return

    # Check for built-in string methods
    if receiver_type == BuiltinType.STRING:
        from stdlib.src.collections.strings import is_builtin_string_method, validate_builtin_string_method_with_validator
        if is_builtin_string_method(call.method):
            validate_builtin_string_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    # Check for built-in stdio methods (stdin, stdout, stderr)
    if receiver_type in [BuiltinType.STDIN, BuiltinType.STDOUT, BuiltinType.STDERR]:
        from stdlib.src.io.stdio import is_builtin_stdio_method, validate_builtin_stdio_method_with_validator
        if is_builtin_stdio_method(call.method):
            validate_builtin_stdio_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    # Check for built-in file methods
    if receiver_type == BuiltinType.FILE:
        from stdlib.src.io.files import is_builtin_file_method, validate_builtin_file_method_with_validator
        if is_builtin_file_method(call.method):
            validate_builtin_file_method_with_validator(call, validator.reporter, validator)
            return

    # Check for built-in Result<T> methods (generic enum after monomorphization)
    if isinstance(receiver_type, EnumType) and receiver_type.name.startswith("Result<"):
        from backend.generics.results import is_builtin_result_method, validate_result_realise_method_with_validator
        if is_builtin_result_method(call.method):
            validate_result_realise_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    # Check for built-in Maybe<T> methods (generic enum after monomorphization)
    if isinstance(receiver_type, EnumType) and receiver_type.name.startswith("Maybe<"):
        from backend.generics.maybe import is_builtin_maybe_method, validate_maybe_method_with_validator
        if is_builtin_maybe_method(call.method):
            validate_maybe_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    # Check for built-in Own<T> methods (generic struct after monomorphization)
    if isinstance(receiver_type, StructType) and receiver_type.name.startswith("Own<"):
        from backend.generics.own import is_builtin_own_method, validate_own_method_with_validator
        if is_builtin_own_method(call.method):
            validate_own_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    # Check for built-in HashMap<K, V> methods (generic struct after monomorphization)
    if isinstance(receiver_type, StructType) and receiver_type.name.startswith("HashMap<"):
        from backend.generics.hashmap import is_builtin_hashmap_method, validate_hashmap_method_with_validator
        if is_builtin_hashmap_method(call.method):
            validate_hashmap_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    # Check for built-in List<T> methods (generic struct after monomorphization)
    if isinstance(receiver_type, StructType) and receiver_type.name.startswith("List<"):
        from backend.generics.list import is_builtin_list_method, validate_list_method_with_validator
        if is_builtin_list_method(call.method):
            validate_list_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    # Check for perk implementation methods FIRST - perks override auto-derived methods
    perk_method = validator.perk_impl_table.get_method(receiver_type, call.method)
    if perk_method is not None:
        # Found a perk method - validate it
        # Validate argument count (receiver is implicit, so compare explicit args)
        expected = len(perk_method.params)
        got = len(call.args)
        if got != expected:
            er.emit(validator.reporter, er.ERR.CE2007, call.loc,
                   method=call.method, expected=expected, got=got)
            return

        # Validate each argument type
        for i, (arg, param) in enumerate(zip(call.args, perk_method.params)):
            validator.validate_expression(arg)
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and param.ty is not None:
                if not types_compatible(validator, arg_type, param.ty):
                    er.emit(validator.reporter, er.ERR.CE2023, arg.loc if hasattr(arg, 'loc') else call.loc,
                           method=call.method, expected=str(param.ty), got=str(arg_type))

        # Store the inferred return type on the call node
        if perk_method.ret is not None:
            call.inferred_return_type = perk_method.ret
        return

    # Check for built-in primitive methods (numeric types, bool, and string)
    if isinstance(receiver_type, BuiltinType) and receiver_type in [
        BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
        BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
    ]:
        from backend.types.primitives import is_builtin_primitive_method, validate_builtin_primitive_method_with_validator
        if is_builtin_primitive_method(call.method):
            validate_builtin_primitive_method_with_validator(call, receiver_type, validator.reporter, validator)
            return

    # Check for auto-derived struct methods (hash) - AFTER perks
    if isinstance(receiver_type, StructType) and call.method == "hash":
        from stdlib.src.common import get_builtin_method
        struct_hash_method = get_builtin_method(receiver_type, "hash")
        if struct_hash_method is not None:
            # Use the registered validator for struct hash
            struct_hash_method.semantic_validator(call, receiver_type, validator.reporter)
            return

    # Check for auto-derived enum methods (hash) - AFTER perks
    if isinstance(receiver_type, EnumType) and call.method == "hash":
        from stdlib.src.common import get_builtin_method
        enum_hash_method = get_builtin_method(receiver_type, "hash")
        if enum_hash_method is not None:
            # Use the registered validator for enum hash
            enum_hash_method.semantic_validator(call, receiver_type, validator.reporter)
            return

    # Look up the extension method
    method = validator.extension_table.get_method(receiver_type, call.method)

    # If not found in regular extensions, check generic extensions
    # For types like Box<i32>, extract base name "Box" and look up generic extension
    if method is None and isinstance(receiver_type, StructType):
        # Check if this is a concrete generic type (e.g., "Box<i32>")
        type_name = receiver_type.name
        if '<' in type_name:
            # Extract base name: "Box<i32>" -> "Box"
            base_name = type_name.split('<')[0]
            generic_method = validator.generic_extension_table.get_method(base_name, call.method)
            if generic_method is not None:
                # Generic extension found - use it as if it were a regular method
                # The method has already been monomorphized and added to extensions
                # during Pass 1.6, so we can validate it here
                method = validator.extension_table.get_method(receiver_type, call.method)

    if method is None:
        # Method doesn't exist for this type
        er.emit(validator.reporter, er.ERR.CE2008, call.loc, name=f"{receiver_type}.{call.method}")
        return

    # Validate argument count (receiver is implicit, so compare explicit args)
    expected_params = method.params
    actual_args = call.args

    if len(actual_args) != len(expected_params):
        er.emit(validator.reporter, er.ERR.CE2009, call.loc,
               name=f"{receiver_type}.{call.method}", expected=len(expected_params), got=len(actual_args))

    # Validate each argument type against corresponding parameter type
    for i, (arg, param) in enumerate(zip(actual_args, expected_params)):
        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Check type compatibility
        if param.ty is not None:  # Skip if parameter has unknown type
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and not types_compatible(validator, arg_type, param.ty):
                er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=str(param.ty), got=str(arg_type))

    # Validate any excess arguments (if more args than params)
    for i in range(len(expected_params), len(actual_args)):
        validator.validate_expression(actual_args[i])


def _check_stdlib_function(validator: 'TypeValidator', call: Call) -> Optional[any]:
    """
    Check if a function call is to a stdlib function.

    Looks up the function in the FunctionTable's stdlib function registry.

    Args:
        validator: Type validator instance
        call: Function call AST node

    Returns:
        Tuple of (module_path, StdlibFunction) if found, None otherwise
    """
    function_name = call.callee.id

    # Try common module paths to find the function
    possible_modules = ["time", "sys/env", "math"]

    for module_path in possible_modules:
        stdlib_func = validator.func_table.lookup_stdlib_function(module_path, function_name)
        if stdlib_func is not None:
            return (module_path, stdlib_func)

    return None


def _validate_stdlib_function(validator: 'TypeValidator', call: Call, module_and_func: tuple) -> None:
    """
    Validate a stdlib function call.

    For now, this just validates that arguments are expressions.
    Full type validation will be added when we integrate stdlib validators properly.

    Args:
        validator: Type validator instance
        call: Function call AST node
        module_and_func: Tuple of (module_path, StdlibFunction)
    """
    module_path, stdlib_func = module_and_func

    # Validate all argument expressions
    args = call.args if hasattr(call, 'args') else []
    for arg in args:
        validator.validate_expression(arg)

    # TODO: Add proper type validation using stdlib validators
    # For now, we're just ensuring the expressions are valid


def _validate_generic_function_call(
    validator: 'TypeValidator',
    call: Call,
    function_name: str
) -> None:
    """Validate generic function call and rewrite to use mangled name.

    Args:
        validator: Type validator instance
        call: Call AST node
        function_name: Generic function name
    """
    from semantics.generics.types import TypeParameter

    # Get generic function definition
    generic_func = validator.generic_func_table.by_name[function_name]

    # Infer type arguments from call site
    type_args = _infer_type_args_from_call_site(validator, call, generic_func)

    if type_args is None:
        # Type inference failed
        er.emit(
            validator.reporter,
            er.ERR.CE2060,
            call.callee.loc,
            name=function_name,
            reason="could not infer type arguments from call site"
        )
        return

    # Generate mangled name
    mangled_name = mangle_function_name(function_name, type_args)

    # Check if monomorphized version exists
    if mangled_name not in validator.func_table.by_name:
        # Should not happen if monomorphization ran correctly
        er.emit(
            validator.reporter,
            er.ERR.CE2061,
            call.callee.loc,
            name=function_name,
            mangled=mangled_name,
            type_args=str(type_args)
        )
        return

    # REWRITE: Update call node to use mangled name
    call.callee.id = mangled_name

    # Get monomorphized function signature
    func_sig = validator.func_table.by_name[mangled_name]

    # Validate arguments against parameters (existing logic)
    _validate_call_arguments(validator, call, func_sig)


def _infer_type_args_from_call_site(
    validator: 'TypeValidator',
    call: Call,
    generic_func
) -> Optional[tuple]:
    """Infer type arguments from call site arguments.

    This is similar to InstantiationCollector but uses the full type checker.

    Args:
        validator: Type validator
        call: Call AST node
        generic_func: Generic function definition

    Returns:
        Tuple of concrete types or None if inference fails
    """
    from typing import Dict
    import sys

    # Build type parameter -> concrete type mapping
    type_param_map: Dict[str, Type] = {}

    # Get call arguments
    call_args = getattr(call, "args", []) or []
    func_params = generic_func.params

    # Check argument count
    if len(call_args) != len(func_params):
        return None

    # Match each argument to corresponding parameter
    for i, (arg_expr, param) in enumerate(zip(call_args, func_params)):
        # Infer argument type using full type checker
        arg_type = validator.infer_expression_type(arg_expr)

        if arg_type is None or isinstance(arg_type, UnknownType):
            return None

        # Unify argument type with parameter type
        if param.ty is None:
            return None

        success = _unify_types_for_inference(param.ty, arg_type, type_param_map)
        if not success:
            return None

    # Check that all type parameters were inferred
    for tp in generic_func.type_params:
        tp_name = tp.name if hasattr(tp, 'name') else str(tp)
        if tp_name not in type_param_map:
            return None

    # Extract type arguments in parameter order and resolve UnknownType
    from semantics.type_resolution import resolve_unknown_type
    type_args = []
    for tp in generic_func.type_params:
        tp_name = tp.name if hasattr(tp, 'name') else str(tp)
        inferred_type = type_param_map[tp_name]
        # Resolve UnknownType to concrete StructType/EnumType if possible
        resolved_type = resolve_unknown_type(inferred_type, validator.struct_table, validator.enum_table)
        type_args.append(resolved_type)

    return tuple(type_args)


def _unify_types_for_inference(
    param_type: Type,
    arg_type: Type,
    type_param_map: Dict[str, Type]
) -> bool:
    """Unify parameter type with argument type for type inference.

    Args:
        param_type: Parameter type (may contain TypeParameter or UnknownType representing type param)
        arg_type: Argument type (concrete)
        type_param_map: Accumulator for type parameter assignments

    Returns:
        True if unification succeeds
    """
    from semantics.generics.types import TypeParameter

    # Case 1: param_type is a type parameter
    if isinstance(param_type, TypeParameter):
        param_name = param_type.name

        if param_name in type_param_map:
            # Must match existing assignment
            return type_param_map[param_name] == arg_type
        else:
            # New assignment
            type_param_map[param_name] = arg_type
            return True

    # Case 2: param_type is UnknownType (might be a type parameter name)
    # This happens when the generic function parameter type is parsed as UnknownType("T")
    if isinstance(param_type, UnknownType):
        param_name = str(param_type)

        if param_name in type_param_map:
            # Must match existing assignment
            return type_param_map[param_name] == arg_type
        else:
            # New assignment
            type_param_map[param_name] = arg_type
            return True

    # Case 3: Both are concrete types - must match
    if param_type == arg_type:
        return True

    # Case 4: Nested generic types (e.g., Container<T>)
    # Handle GenericTypeRef unified with concrete monomorphized type
    from semantics.generics.types import GenericTypeRef

    if isinstance(param_type, GenericTypeRef):
        param_base = param_type.base_name
        param_type_args = param_type.type_args

        # Check if arg_type is a monomorphized generic with metadata
        if isinstance(arg_type, (StructType, EnumType)):
            # Use generic metadata if available
            if arg_type.generic_base is not None and arg_type.generic_args is not None:
                # Check base names match
                if param_base != arg_type.generic_base:
                    return False

                # Check type argument counts match
                if len(param_type_args) != len(arg_type.generic_args):
                    return False

                # Recursively unify each type argument
                for param_arg, concrete_arg in zip(param_type_args, arg_type.generic_args):
                    if not _unify_types_for_inference(param_arg, concrete_arg, type_param_map):
                        return False

                return True

        # If arg_type is also a GenericTypeRef, unify them directly
        elif isinstance(arg_type, GenericTypeRef):
            arg_base = arg_type.base_name
            arg_type_args = arg_type.type_args

            # Base names must match
            if param_base != arg_base:
                return False

            # Type argument counts must match
            if len(param_type_args) != len(arg_type_args):
                return False

            # Recursively unify each type argument pair
            for param_arg, arg_arg in zip(param_type_args, arg_type_args):
                if not _unify_types_for_inference(param_arg, arg_arg, type_param_map):
                    return False

            return True

    return False




def _validate_call_arguments(
    validator: 'TypeValidator',
    call: Call,
    func_sig
) -> None:
    """Validate call arguments against function signature.

    This is the existing argument validation logic, extracted for reuse.

    Args:
        validator: Type validator
        call: Call AST node
        func_sig: Function signature
    """
    expected_params = func_sig.params
    actual_args = call.args

    # Check argument count
    if len(actual_args) != len(expected_params):
        er.emit(validator.reporter, er.ERR.CE2009, call.callee.loc,
               name=func_sig.name, expected=len(expected_params), got=len(actual_args))
        # Still continue with validation of provided arguments

    # Validate each argument type against corresponding parameter type
    for i, (arg, param) in enumerate(zip(actual_args, expected_params)):
        # Propagate expected types to DotCall nodes for generic enums (before validation)
        # This allows Maybe.None(), Result.Ok(), etc. to work as function arguments
        propagate_enum_type_to_dotcall(validator, arg, param.ty)

        # Propagate expected types to DotCall nodes for generic structs (before validation)
        # This allows Own.alloc(42) to work as function arguments
        propagate_struct_type_to_dotcall(validator, arg, param.ty)

        # Propagate expected types to Call nodes for generic struct constructors
        # This allows Box(42) to work when parameter expects Box<i32>
        if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(param.ty, StructType):
            struct_name = arg.callee.id
            # Check if this is a generic struct constructor
            if struct_name in validator.generic_struct_table.by_name:
                # Update the Call node's callee id to use the concrete type name
                arg.callee.id = param.ty.name

        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Check type compatibility
        if param.ty is not None:  # Skip if parameter has unknown type
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and not types_compatible(validator, arg_type, param.ty):
                er.emit(validator.reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=str(param.ty), got=str(arg_type))

    # Validate any excess arguments (if more args than params)
    for i in range(len(expected_params), len(actual_args)):
        validator.validate_expression(actual_args[i])
