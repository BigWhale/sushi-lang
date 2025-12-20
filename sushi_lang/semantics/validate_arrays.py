"""
Built-in extension methods for array types (fixed and dynamic arrays).

This module implements all built-in array operations for the Sushi language,
including both fixed-size stack arrays and dynamic heap-allocated arrays.

Fixed Array Methods (ArrayType):
- len(): Returns compile-time array length (i32)
- get(index): Safe array access with bounds checking
- iter(): Create iterator for foreach loops
- hash(): Compute hash value (u64)
- fill(value): Fill all elements with a value
- reverse(): Reverse array in-place

Dynamic Array Methods (DynamicArrayType):
- len(): Returns current length (i32)
- capacity(): Returns current capacity (i32)
- get(index): Safe array access with bounds checking
- push(element): Append element, growing capacity if needed
- pop(): Remove and return last element
- fill(value): Fill all elements with a value
- reverse(): Reverse array in-place
- destroy(): Explicitly free memory and make unusable (sets data to null)
- free(): Recursively destroy elements and reset to empty state (still usable)
- iter(): Create iterator for foreach loops
- clone(): Deep copy with independent memory
- hash(): Compute hash value (u64)

u8[] Specific Methods:
- to_string(): Zero-cost conversion to string (assumes valid UTF-8)
  WARNING: No validation performed. Invalid UTF-8 = undefined behavior.
  Future: Use bytes_to_string_checked() from stdlib for validated conversion.
"""

from typing import Any
from sushi_lang.semantics.ast import MethodCall, Name, IntLit
from sushi_lang.semantics.typesys import Type, ArrayType, DynamicArrayType, BuiltinType, IteratorType
import llvmlite.ir as ir
from sushi_lang.internals import errors as er
from sushi_lang.sushi_stdlib.src.common import register_builtin_method, BuiltinMethod


def _is_integer_type(type_: Type) -> bool:
    """Check if a type is any integer type (signed or unsigned)."""
    return type_ in (
        BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64
    )


def _validate_fixed_array_len(call: MethodCall, array_type: ArrayType, reporter: Any) -> None:
    """Validate len() method call on fixed arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.len", expected=0, got=len(call.args))


def _validate_fixed_array_get(call: MethodCall, array_type: ArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate get(index) method call on fixed arrays."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.get", expected=1, got=len(call.args))
        return

    # Validate argument is any integer type using the validator if available
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and not _is_integer_type(arg_type):
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="integer type", got=str(arg_type))

    # Compile-time bounds checking for fixed arrays with constant indices
    if isinstance(call.args[0], IntLit):
        index_value = call.args[0].value
        array_size = array_type.size

        # Check for out-of-bounds access (negative indices or >= array size)
        if index_value < 0 or index_value >= array_size:
            er.emit(reporter, er.ERR.CE2012, call.args[0].loc,
                   index=index_value, size=array_size)


def _validate_dynamic_array_len(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate len() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.len", expected=0, got=len(call.args))


def _validate_dynamic_array_capacity(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate capacity() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.capacity", expected=0, got=len(call.args))


def _validate_dynamic_array_get(call: MethodCall, array_type: DynamicArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate get(index) method call on dynamic arrays."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.get", expected=1, got=len(call.args))
        return

    # Validate argument is any integer type using the validator if available
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and not _is_integer_type(arg_type):
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected="integer type", got=str(arg_type))


def _validate_dynamic_array_push(call: MethodCall, array_type: DynamicArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate push(element) method call on dynamic arrays."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.push", expected=1, got=len(call.args))
        return

    # Validate argument type matches array element type
    if validator:
        # Propagate expected type to DotCall nodes for generic enums
        # This allows arr.push(Maybe.None()) where arr is Maybe<T>[]
        from sushi_lang.semantics.passes.types.utils import propagate_enum_type_to_dotcall
        propagate_enum_type_to_dotcall(validator, call.args[0], array_type.base_type)

        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != array_type.base_type:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected=str(array_type.base_type), got=str(arg_type))


def _validate_dynamic_array_pop(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate pop() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.pop", expected=0, got=len(call.args))


def _validate_dynamic_array_destroy(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate destroy() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.destroy", expected=0, got=len(call.args))


def _validate_dynamic_array_free(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate free() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.free", expected=0, got=len(call.args))


def _validate_array_iter(call: MethodCall, array_type: ArrayType | DynamicArrayType, reporter: Any) -> None:
    """Validate iter() method call on arrays (both fixed and dynamic)."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.iter", expected=0, got=len(call.args))


def _validate_byte_array_to_string(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate to_string() method call on u8[] byte arrays.

    This is a zero-cost conversion that assumes valid UTF-8 bytes.
    No UTF-8 validation is performed. Invalid UTF-8 results in undefined behavior.

    Future: A stdlib bytes_to_string_checked() function will provide validation.
    """
    # Only available on u8[] (byte arrays)
    if array_type.base_type != BuiltinType.U8:
        er.emit(reporter, er.ERR.CE2023, call.loc,
               method="to_string", expected="u8[]", got=str(array_type))
        return

    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.to_string", expected=0, got=len(call.args))


def _validate_dynamic_array_clone(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate clone() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.clone", expected=0, got=len(call.args))


def _validate_fixed_array_fill(call: MethodCall, array_type: ArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate fill(value) method call on fixed arrays."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.fill", expected=1, got=len(call.args))
        return

    # Validate argument type matches array element type
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != array_type.base_type:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected=str(array_type.base_type), got=str(arg_type))


def _validate_dynamic_array_fill(call: MethodCall, array_type: DynamicArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate fill(value) method call on dynamic arrays."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.fill", expected=1, got=len(call.args))
        return

    # Validate argument type matches array element type
    if validator:
        validator.validate_expression(call.args[0])
        arg_type = validator.infer_expression_type(call.args[0])
        if arg_type is not None and arg_type != array_type.base_type:
            er.emit(reporter, er.ERR.CE2006, call.args[0].loc,
                   index=1, expected=str(array_type.base_type), got=str(arg_type))


def _validate_fixed_array_reverse(call: MethodCall, array_type: ArrayType, reporter: Any) -> None:
    """Validate reverse() method call on fixed arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.reverse", expected=0, got=len(call.args))


def _validate_dynamic_array_reverse(call: MethodCall, array_type: DynamicArrayType, reporter: Any) -> None:
    """Validate reverse() method call on dynamic arrays."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{array_type}.reverse", expected=0, got=len(call.args))


# LLVM emission functions
def is_builtin_array_method(method_name: str) -> bool:
    """Check if a method name is a built-in array method."""
    # Fixed array methods: len, get, iter, hash, fill, reverse
    # Dynamic array methods: len, get, push, pop, capacity, destroy, free, iter, clone, hash, fill, reverse
    # u8[] specific methods: to_string
    return method_name in {"len", "get", "push", "pop", "capacity", "destroy", "free", "iter", "to_string", "clone", "hash", "fill", "reverse"}


def validate_builtin_array_method(call: MethodCall, array_type: ArrayType | DynamicArrayType, reporter: Any, validator: Any = None) -> None:
    """Validate built-in array method calls with optional validator for type checking.

    Args:
        call: The method call to validate.
        array_type: The array type (fixed or dynamic).
        reporter: The error reporter for emitting validation errors.
        validator: Optional type validator for checking argument types.
    """
    method_name = call.method

    if method_name == "len":
        if isinstance(array_type, ArrayType):
            _validate_fixed_array_len(call, array_type, reporter)
        else:
            _validate_dynamic_array_len(call, array_type, reporter)

    elif method_name == "capacity":
        # capacity() - only available on dynamic arrays
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="capacity", expected="dynamic array", got=str(array_type))
            return
        _validate_dynamic_array_capacity(call, array_type, reporter)

    elif method_name == "get":
        if isinstance(array_type, ArrayType):
            _validate_fixed_array_get(call, array_type, reporter, validator)
        else:
            _validate_dynamic_array_get(call, array_type, reporter, validator)

    elif method_name == "push":
        # push(element) - only available on dynamic arrays
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="push", expected="dynamic array", got=str(array_type))
            return
        _validate_dynamic_array_push(call, array_type, reporter, validator)

    elif method_name == "pop":
        # pop() - only available on dynamic arrays
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="pop", expected="dynamic array", got=str(array_type))
            return
        _validate_dynamic_array_pop(call, array_type, reporter)

    elif method_name == "destroy":
        # destroy() - only available on dynamic arrays
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="destroy", expected="dynamic array", got=str(array_type))
            return
        _validate_dynamic_array_destroy(call, array_type, reporter)

    elif method_name == "free":
        # free() - only available on dynamic arrays
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="free", expected="dynamic array", got=str(array_type))
            return
        _validate_dynamic_array_free(call, array_type, reporter)

    elif method_name == "iter":
        # iter() - available on both fixed and dynamic arrays
        _validate_array_iter(call, array_type, reporter)

    elif method_name == "to_string":
        # to_string() - only available on u8[] (byte arrays)
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="to_string", expected="u8[]", got=str(array_type))
            return
        _validate_byte_array_to_string(call, array_type, reporter)

    elif method_name == "clone":
        # clone() - only available on dynamic arrays
        if not isinstance(array_type, DynamicArrayType):
            er.emit(reporter, er.ERR.CE2023, call.loc,
                   method="clone", expected="dynamic array", got=str(array_type))
            return
        _validate_dynamic_array_clone(call, array_type, reporter)

    elif method_name == "hash":
        # hash() - available on both fixed and dynamic arrays (no arguments)
        if call.args:
            er.emit(reporter, er.ERR.CE2009, call.loc,
                   name=f"{array_type}.hash", expected=0, got=len(call.args))

    elif method_name == "fill":
        # fill(value) - available on both fixed and dynamic arrays
        if isinstance(array_type, ArrayType):
            _validate_fixed_array_fill(call, array_type, reporter, validator)
        else:
            _validate_dynamic_array_fill(call, array_type, reporter, validator)

    elif method_name == "reverse":
        # reverse() - available on both fixed and dynamic arrays
        if isinstance(array_type, ArrayType):
            _validate_fixed_array_reverse(call, array_type, reporter)
        else:
            _validate_dynamic_array_reverse(call, array_type, reporter)


def get_builtin_array_method_return_type(method_name: str, array_type: ArrayType | DynamicArrayType) -> Type | None:
    """Get the return type of a built-in array method."""
    if method_name == "len":
        return BuiltinType.I32
    elif method_name == "capacity":
        # Only available on dynamic arrays
        if isinstance(array_type, DynamicArrayType):
            return BuiltinType.I32
        return None
    elif method_name == "get":
        return array_type.base_type
    elif method_name == "push":
        # Only available on dynamic arrays, returns blank type
        if isinstance(array_type, DynamicArrayType):
            return BuiltinType.BLANK
        return None
    elif method_name == "pop":
        # Only available on dynamic arrays, returns element type
        if isinstance(array_type, DynamicArrayType):
            return array_type.base_type
        return None
    elif method_name == "destroy":
        # Only available on dynamic arrays, returns blank type
        if isinstance(array_type, DynamicArrayType):
            return BuiltinType.BLANK
        return None
    elif method_name == "free":
        # Only available on dynamic arrays, returns blank type
        if isinstance(array_type, DynamicArrayType):
            return BuiltinType.BLANK
        return None
    elif method_name == "iter":
        # Available on both fixed and dynamic arrays, returns Iterator<element_type>
        return IteratorType(element_type=array_type.base_type)
    elif method_name == "to_string":
        # Only available on u8[] (byte arrays), returns string
        if isinstance(array_type, DynamicArrayType) and array_type.base_type == BuiltinType.U8:
            return BuiltinType.STRING
        return None
    elif method_name == "clone":
        # Only available on dynamic arrays, returns same array type
        if isinstance(array_type, DynamicArrayType):
            return array_type
        return None
    elif method_name == "hash":
        # Available on both fixed and dynamic arrays, returns u64
        return BuiltinType.U64
    elif method_name == "fill":
        # Available on both fixed and dynamic arrays, returns blank type
        return BuiltinType.BLANK
    elif method_name == "reverse":
        # Available on both fixed and dynamic arrays, returns blank type
        return BuiltinType.BLANK
    return None
