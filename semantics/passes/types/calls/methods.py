# semantics/passes/types/calls/methods.py
"""
Method call validation.

Handles validation for:
- Extension methods
- Built-in methods (arrays, strings, primitives)
- Generic struct methods (Own<T>, HashMap<K,V>, List<T>)
- Perk implementation methods
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from internals import errors as er
from semantics.typesys import BuiltinType, ArrayType, DynamicArrayType, EnumType, StructType
from semantics.ast import MethodCall, Name
from ..compatibility import types_compatible
from ..utils import is_array_destroyed, mark_array_destroyed

if TYPE_CHECKING:
    from .. import TypeValidator


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
                from stdlib.generics.collections.hashmap import is_builtin_hashmap_method
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
    # Also allow ResultType through for Result<T, E> method validation
    from semantics.typesys import ResultType
    if not isinstance(receiver_type, (BuiltinType, ArrayType, DynamicArrayType, EnumType, StructType, ResultType)) and not is_generic_struct:
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

    # Check for built-in Result<T, E> methods
    # Handle both EnumType (monomorphized Result<T, E>) and ResultType (semantic type)
    from semantics.typesys import ResultType
    if isinstance(receiver_type, ResultType):
        # Convert ResultType to EnumType for method validation
        from backend.generics.results import is_builtin_result_method, validate_result_method_with_validator, ensure_result_type_in_table

        # ALWAYS ensure Result<T, E> enum exists in the table (for hash, builtin methods, etc.)
        result_enum = ensure_result_type_in_table(validator.enum_table, receiver_type.ok_type, receiver_type.err_type)

        if is_builtin_result_method(call.method):
            # Builtin Result method (is_ok, is_err, realise, expect, err)
            if result_enum:
                validate_result_method_with_validator(call, result_enum, validator.reporter, validator)
            return
        else:
            # Other methods (hash, etc.) - replace ResultType with EnumType for downstream lookup
            # This allows hash and other auto-derived methods to work on Result types
            if result_enum:
                receiver_type = result_enum
            # Fall through to generic method lookup
    elif isinstance(receiver_type, EnumType) and receiver_type.name.startswith("Result<"):
        from backend.generics.results import is_builtin_result_method, validate_result_method_with_validator
        if is_builtin_result_method(call.method):
            validate_result_method_with_validator(call, receiver_type, validator.reporter, validator)
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
        from stdlib.generics.collections.hashmap import is_builtin_hashmap_method, validate_hashmap_method_with_validator
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
