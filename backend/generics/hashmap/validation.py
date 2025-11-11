"""
Validation logic for HashMap<K, V> method calls.

This module provides semantic validation for all HashMap<K, V> methods during
compilation. It ensures correct argument counts, types, and expressions before
LLVM IR emission.
"""

from typing import Any, Optional
from semantics.ast import MethodCall, Call
from semantics.typesys import StructType, Type, BuiltinType
from internals import errors as er
from internals.errors import raise_internal_error


def is_builtin_hashmap_method(method_name: str) -> bool:
    """Check if a method name is a builtin HashMap<K, V> method.

    Args:
        method_name: The name of the method to check.

    Returns:
        True if this is a recognized HashMap<K, V> method, False otherwise.
    """
    return method_name in (
        "new", "insert", "get", "contains_key", "remove",
        "len", "is_empty", "tombstone_count", "rehash", "free", "destroy", "debug",
        "keys", "values"
    )


def validate_hashmap_method_with_validator(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V> method calls.

    Routes to specific validation functions based on method name.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    method = call.method

    if method == "new":
        _validate_hashmap_new(call, hashmap_type, reporter)
    elif method == "insert":
        _validate_hashmap_insert(call, hashmap_type, reporter, validator)
    elif method == "get":
        _validate_hashmap_get(call, hashmap_type, reporter, validator)
    elif method == "contains_key":
        _validate_hashmap_contains_key(call, hashmap_type, reporter, validator)
    elif method == "remove":
        _validate_hashmap_remove(call, hashmap_type, reporter, validator)
    elif method == "len":
        _validate_hashmap_len(call, hashmap_type, reporter)
    elif method == "is_empty":
        _validate_hashmap_is_empty(call, hashmap_type, reporter)
    elif method == "tombstone_count":
        _validate_hashmap_tombstone_count(call, hashmap_type, reporter)
    elif method == "rehash":
        _validate_hashmap_rehash(call, hashmap_type, reporter)
    elif method == "free":
        _validate_hashmap_free(call, hashmap_type, reporter)
    elif method == "destroy":
        _validate_hashmap_destroy(call, hashmap_type, reporter)
    elif method == "debug":
        _validate_hashmap_debug(call, hashmap_type, reporter)
    elif method == "keys":
        _validate_hashmap_keys(call, hashmap_type, reporter)
    elif method == "values":
        _validate_hashmap_values(call, hashmap_type, reporter)
    else:
        # Unknown method - should not happen if is_builtin_hashmap_method was called first
        raise_internal_error("CE0085", method=method)


# ==============================================================================
# Type Parsing Helpers
# ==============================================================================


def parse_hashmap_types(hashmap_type: StructType, validator: Any) -> tuple[Optional[Type], Optional[Type]]:
    """Parse K and V types from HashMap<K, V> type name.

    Args:
        hashmap_type: The HashMap<K, V> struct type.
        validator: Type validator for looking up types.

    Returns:
        Tuple of (key_type, value_type) or (None, None) if parsing fails.
    """
    from semantics.typesys import BuiltinType, EnumType

    # Extract K and V types from HashMap<K, V>
    if not hashmap_type.name.startswith("HashMap<"):
        return None, None

    # Parse the type parameters (handle nested types like HashMap<Pair<i32, string>, bool>)
    type_params_str = hashmap_type.name[8:-1]  # Remove "HashMap<" and ">"

    # Find the comma that separates K and V (need to handle nested brackets)
    bracket_depth = 0
    comma_pos = -1
    for i, c in enumerate(type_params_str):
        if c == '<':
            bracket_depth += 1
        elif c == '>':
            bracket_depth -= 1
        elif c == ',' and bracket_depth == 0:
            comma_pos = i
            break

    if comma_pos == -1:
        return None, None

    key_type_str = type_params_str[:comma_pos].strip()
    value_type_str = type_params_str[comma_pos + 1:].strip()

    # Resolve type strings to actual Type objects
    key_type = _resolve_type_string(key_type_str, validator)
    value_type = _resolve_type_string(value_type_str, validator)

    return key_type, value_type


def _resolve_type_string(type_str: str, validator: Any) -> Optional[Type]:
    """Resolve a type string (e.g., "i32", "Maybe<i32>", "Pair<i32, string>") to a Type object.

    Args:
        type_str: The type string to resolve.
        validator: Type validator with access to struct/enum tables.

    Returns:
        The resolved Type object or None if not found.
    """
    from semantics.typesys import BuiltinType, EnumType

    # Check for built-in types
    builtin_map = {
        'i8': BuiltinType.I8, 'i16': BuiltinType.I16, 'i32': BuiltinType.I32, 'i64': BuiltinType.I64,
        'u8': BuiltinType.U8, 'u16': BuiltinType.U16, 'u32': BuiltinType.U32, 'u64': BuiltinType.U64,
        'f32': BuiltinType.F32, 'f64': BuiltinType.F64,
        'bool': BuiltinType.BOOL, 'string': BuiltinType.STRING,
    }
    if type_str in builtin_map:
        return builtin_map[type_str]

    # Check for enum types (including generic enums like Maybe<i32>, Result<i32>)
    if hasattr(validator, 'enum_table') and type_str in validator.enum_table.by_name:
        return validator.enum_table.by_name[type_str]

    # Check for struct types (including generic structs like Pair<i32, string>)
    if hasattr(validator, 'struct_table') and type_str in validator.struct_table.by_name:
        return validator.struct_table.by_name[type_str]

    # Type not found
    return None


# ==============================================================================
# Individual Method Validators
# ==============================================================================


def _validate_hashmap_new(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.new() method call.

    Validates that no arguments are provided and that the key type K supports
    hashing and equality comparison.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    from stdlib.src.common import get_builtin_method
    from .utils import emit_key_equality_check

    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="new", expected=0, got=len(call.args))

    # Extract K type from HashMap<K, V>
    # We need a minimal validator object with empty tables for parse_hashmap_types
    # Since we're at HashMap.new() time, we don't have a validator passed in
    # We need to parse the type name directly
    from semantics.typesys import EnumType

    # Parse K type from HashMap<K, V> name
    if not hashmap_type.name.startswith("HashMap<"):
        return

    # Extract type parameters
    type_params_str = hashmap_type.name[8:-1]  # Remove "HashMap<" and ">"

    # Find the comma that separates K and V (handle nested brackets)
    bracket_depth = 0
    comma_pos = -1
    for i, c in enumerate(type_params_str):
        if c == '<':
            bracket_depth += 1
        elif c == '>':
            bracket_depth -= 1
        elif c == ',' and bracket_depth == 0:
            comma_pos = i
            break

    if comma_pos == -1:
        return

    key_type_str = type_params_str[:comma_pos].strip()

    # We need to find the actual Type object for the key type
    # Since we don't have a validator here, we need to look up the type differently
    # We can check if it's in the struct_table that's part of hashmap_type's context
    # For now, let's use a simpler approach: check the fields of the HashMap struct
    # The HashMap struct has a "keys" field which is K[]
    key_type = None
    for field_name, field_type in hashmap_type.fields:
        if field_name == "keys":
            # This is K[] (DynamicArrayType), extract the element type
            from semantics.typesys import DynamicArrayType
            if isinstance(field_type, DynamicArrayType):
                key_type = field_type.element_type
                break

    if key_type is None:
        # Could not extract key type, skip validation
        return

    # Validate that K has .hash() method
    hash_method = get_builtin_method(key_type, "hash")
    if hash_method is None:
        er.emit(reporter, er.ERR.CE2054, call.loc, key_type=str(key_type))

    # Validate that K supports equality comparison
    # Check if key_type is one of the supported types in emit_key_equality_check
    from semantics.typesys import ArrayType, DynamicArrayType
    supported_equality = (
        key_type in (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                     BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
                     BuiltinType.BOOL, BuiltinType.F32, BuiltinType.F64, BuiltinType.STRING) or
        isinstance(key_type, StructType) or
        isinstance(key_type, EnumType) or
        isinstance(key_type, (ArrayType, DynamicArrayType))
    )

    if not supported_equality:
        er.emit(reporter, er.ERR.CE2055, call.loc, key_type=str(key_type))


def _validate_hashmap_insert(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V>.insert(key, value) method call.

    Validates argument count, types, and expressions using the standard
    validation utilities from semantics.passes.types.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    from semantics.passes.types.utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall
    from semantics.passes.types.compatibility import types_compatible

    # Validate argument count
    if len(call.args) != 2:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="insert", expected=2, got=len(call.args))
        return

    # Extract K and V types from HashMap<K, V>
    key_type, value_type = parse_hashmap_types(hashmap_type, validator)
    if key_type is None or value_type is None:
        # Couldn't parse types - just validate expressions without type checking
        for arg in call.args:
            validator.validate_expression(arg)
        return

    # Validate each argument with type propagation (reuse standard validation logic)
    expected_types = [key_type, value_type]
    for i, (arg, expected_ty) in enumerate(zip(call.args, expected_types)):
        # Propagate expected types to DotCall nodes for generic enums (before validation)
        # This allows Maybe.None(), Result.Ok(), etc. to work as function arguments
        propagate_enum_type_to_dotcall(validator, arg, expected_ty)

        # Propagate expected types to DotCall nodes for generic structs (before validation)
        # This allows Own.alloc(42) to work as function arguments
        propagate_struct_type_to_dotcall(validator, arg, expected_ty)

        # Propagate expected types to Call nodes for generic struct constructors
        # This allows Box(42) to work when parameter expects Box<i32>
        if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(expected_ty, StructType):
            struct_name = arg.callee.id
            # Check if this is a generic struct constructor
            if struct_name in validator.generic_struct_table.by_name:
                # Update the Call node's callee id to use the concrete type name
                arg.callee.id = expected_ty.name

        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Check type compatibility
        if expected_ty is not None:
            arg_type = validator.infer_expression_type(arg)
            if arg_type is not None and not types_compatible(validator, arg_type, expected_ty):
                er.emit(reporter, er.ERR.CE2006, arg.loc,
                       index=i+1, expected=str(expected_ty), got=str(arg_type))


def _validate_hashmap_get(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V>.get(key) method call.

    Validates argument count and key type.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    _validate_hashmap_key_method(call, hashmap_type, reporter, validator, method_name="get")


def _validate_hashmap_contains_key(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V>.contains_key(key) method call.

    Validates argument count and key type.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    _validate_hashmap_key_method(call, hashmap_type, reporter, validator, method_name="contains_key")


def _validate_hashmap_remove(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate HashMap<K, V>.remove(key) method call.

    Validates argument count and key type.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    _validate_hashmap_key_method(call, hashmap_type, reporter, validator, method_name="remove")


def _validate_hashmap_key_method(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any,
    validator: Any,
    method_name: str
) -> None:
    """Validate HashMap<K, V> methods that take a key argument (get, contains_key, remove).

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
        method_name: The name of the method being validated.
    """
    from semantics.passes.types.utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall
    from semantics.passes.types.compatibility import types_compatible

    # Validate argument count
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2016, call.loc, method=method_name, expected=1, got=len(call.args))
        return

    # Extract K type from HashMap<K, V>
    key_type, _ = parse_hashmap_types(hashmap_type, validator)
    if key_type is None:
        # Couldn't parse types - just validate expressions without type checking
        validator.validate_expression(call.args[0])
        return

    # Validate the key argument with type propagation
    arg = call.args[0]

    # Propagate expected types to DotCall nodes for generic enums
    propagate_enum_type_to_dotcall(validator, arg, key_type)

    # Propagate expected types to DotCall nodes for generic structs
    propagate_struct_type_to_dotcall(validator, arg, key_type)

    # Propagate expected types to Call nodes for generic struct constructors
    if isinstance(arg, Call) and hasattr(arg.callee, 'id') and isinstance(key_type, StructType):
        struct_name = arg.callee.id
        if struct_name in validator.generic_struct_table.by_name:
            arg.callee.id = key_type.name

    # Recursively validate the argument expression
    validator.validate_expression(arg)

    # Check type compatibility
    if key_type is not None:
        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, key_type):
            er.emit(reporter, er.ERR.CE2006, arg.loc,
                   index=1, expected=str(key_type), got=str(arg_type))


def _validate_hashmap_len(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.len() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="len", expected=0, got=len(call.args))


def _validate_hashmap_is_empty(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.is_empty() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="is_empty", expected=0, got=len(call.args))


def _validate_hashmap_tombstone_count(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.tombstone_count() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="tombstone_count", expected=0, got=len(call.args))


def _validate_hashmap_rehash(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.rehash() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="rehash", expected=0, got=len(call.args))


def _validate_hashmap_free(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.free() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="free", expected=0, got=len(call.args))


def _validate_hashmap_destroy(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.destroy() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="destroy", expected=0, got=len(call.args))


def _validate_hashmap_debug(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.debug() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="debug", expected=0, got=len(call.args))


def _validate_hashmap_keys(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.keys() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="keys", expected=0, got=len(call.args))


def _validate_hashmap_values(
    call: MethodCall,
    hashmap_type: StructType,
    reporter: Any
) -> None:
    """Validate HashMap<K, V>.values() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        hashmap_type: The HashMap<K, V> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="values", expected=0, got=len(call.args))
