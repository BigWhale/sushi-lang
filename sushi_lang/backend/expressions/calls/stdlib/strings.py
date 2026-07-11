"""
Standard library string method call emission.

This module handles external calls to precompiled stdlib string functions
used when the collections/strings module is imported via use <collections/strings>.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_stdlib_string_call(
    codegen: 'LLVMCodegen',
    method: str,
    receiver_value: ir.Value,
    args: list,
    to_i1: bool
) -> ir.Value:
    """Emit a call to a stdlib string method.

    This function emits an external call to a precompiled stdlib function
    instead of emitting inline IR. Used when collections/strings module
    is imported via use <collections/strings> syntax.

    Args:
        codegen: The LLVM code generator
        method: The method name (e.g., "len", "concat", "contains")
        receiver_value: The LLVM value of the receiver (string fat pointer struct)
        args: Method arguments
        to_i1: Whether to convert result to i1 boolean

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the method is not implemented in stdlib
    """
    builder = require_builder(codegen)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i8_ptr = ir.IntType(INT8_BIT_WIDTH).as_pointer()
    string_type = codegen.types.string_struct  # {i8* data, i32 size}

    # Build function name: string_{method}
    func_name = f"string_{method}"

    # Determine return type and parameter types based on method
    if method in ("len", "size"):
        # len() -> i32, size() -> i32
        return_type = i32
        param_types = [string_type]
        call_args = [receiver_value]
    elif method == "concat":
        # concat(string other) -> string
        return_type = string_type
        param_types = [string_type, string_type]
        other_value = codegen.expressions.emit_expr(args[0])
        call_args = [receiver_value, other_value]
    elif method in ("contains", "starts_with", "ends_with"):
        # These methods: (string) -> bool (i8)
        return_type = i8
        param_types = [string_type, string_type]
        arg_value = codegen.expressions.emit_expr(args[0])
        call_args = [receiver_value, arg_value]
    elif method == "count":
        # count(string needle) -> i32
        return_type = i32
        param_types = [string_type, string_type]
        arg_value = codegen.expressions.emit_expr(args[0])
        call_args = [receiver_value, arg_value]
    elif method in ("sleft", "sright", "char_at", "repeat"):
        # These methods: (i32) -> string
        return_type = string_type
        param_types = [string_type, i32]
        arg_value = codegen.expressions.emit_expr(args[0])
        call_args = [receiver_value, arg_value]
    elif method in ("s", "ss"):
        # These methods: (i32, i32) -> string
        return_type = string_type
        param_types = [string_type, i32, i32]
        arg1_value = codegen.expressions.emit_expr(args[0])
        arg2_value = codegen.expressions.emit_expr(args[1])
        call_args = [receiver_value, arg1_value, arg2_value]
    elif method in ("upper", "lower", "cap", "trim", "tleft", "tright", "reverse"):
        # No-arg methods that return string
        return_type = string_type
        param_types = [string_type]
        call_args = [receiver_value]
    elif method == "to_bytes":
        # to_bytes() -> u8[] (struct by value)
        # Array struct: {i32 len, i32 cap, u8* data}
        # NOTE: Stdlib returns struct by value, not pointer
        array_struct_type = ir.LiteralStructType([i32, i32, i8_ptr])
        return_type = array_struct_type
        param_types = [string_type]
        call_args = [receiver_value]
    elif method == "split":
        # split(string delimiter) -> string[] (struct by value)
        # Array struct: {i32 len, i32 cap, string* data}
        # NOTE: Stdlib returns struct by value, not pointer
        string_struct_ptr = string_type.as_pointer()
        array_struct_type = ir.LiteralStructType([i32, i32, string_struct_ptr])
        return_type = array_struct_type
        param_types = [string_type, string_type]
        arg_value = codegen.expressions.emit_expr(args[0])
        call_args = [receiver_value, arg_value]
    elif method == "join":
        # join(string[] parts) -> string
        # Array struct: {i32 len, i32 cap, string* data}
        # Takes array struct by value (loaded from stack pointer)
        return_type = string_type
        string_struct_ptr = string_type.as_pointer()
        array_struct_type = ir.LiteralStructType([i32, i32, string_struct_ptr])
        param_types = [string_type, array_struct_type]
        arg_expr = args[0]
        array_ptr_value = codegen.expressions.emit_expr(arg_expr)
        # Check if we already have the value or need to load
        if isinstance(array_ptr_value.type, ir.PointerType):
            array_value = codegen.builder.load(array_ptr_value, name="array_value")
        else:
            array_value = array_ptr_value
        call_args = [receiver_value, array_value]
    elif method == "replace":
        # replace(string old, string new) -> string
        return_type = string_type
        param_types = [string_type, string_type, string_type]
        old_value = codegen.expressions.emit_expr(args[0])
        new_value = codegen.expressions.emit_expr(args[1])
        call_args = [receiver_value, old_value, new_value]
    elif method == "pad_left":
        # pad_left(i32 width, string pad_char) -> string
        return_type = string_type
        param_types = [string_type, i32, string_type]
        width_value = codegen.expressions.emit_expr(args[0])
        pad_char_value = codegen.expressions.emit_expr(args[1])
        call_args = [receiver_value, width_value, pad_char_value]
    elif method == "pad_right":
        # pad_right(i32 width, string pad_char) -> string
        return_type = string_type
        param_types = [string_type, i32, string_type]
        width_value = codegen.expressions.emit_expr(args[0])
        pad_char_value = codegen.expressions.emit_expr(args[1])
        call_args = [receiver_value, width_value, pad_char_value]
    elif method == "strip_prefix":
        # strip_prefix(string prefix) -> string
        return_type = string_type
        param_types = [string_type, string_type]
        prefix_value = codegen.expressions.emit_expr(args[0])
        call_args = [receiver_value, prefix_value]
    elif method == "strip_suffix":
        # strip_suffix(string suffix) -> string
        return_type = string_type
        param_types = [string_type, string_type]
        suffix_value = codegen.expressions.emit_expr(args[0])
        call_args = [receiver_value, suffix_value]
    elif method == "find":
        # find(string needle) -> Maybe<i32> (enum struct)
        # Maybe<i32> layout: {i32 tag, [4 x i8] data}
        # tag = 0 for Some, tag = 1 for None
        i8_array_4 = ir.ArrayType(i8, 4)
        maybe_i32_type = ir.LiteralStructType([i32, i8_array_4])
        return_type = maybe_i32_type
        param_types = [string_type, string_type]
        arg_value = codegen.expressions.emit_expr(args[0])
        call_args = [receiver_value, arg_value]
    elif method == "find_last":
        # find_last(string needle) -> Maybe<i32> (enum struct)
        # Maybe<i32> layout: {i32 tag, [4 x i8] data}
        # tag = 0 for Some, tag = 1 for None
        i8_array_4 = ir.ArrayType(i8, 4)
        maybe_i32_type = ir.LiteralStructType([i32, i8_array_4])
        return_type = maybe_i32_type
        param_types = [string_type, string_type]
        arg_value = codegen.expressions.emit_expr(args[0])
        call_args = [receiver_value, arg_value]
    elif method == "to_i32":
        # to_i32() -> Maybe<i32> (enum struct)
        # Maybe<i32> layout: {i32 tag, [4 x i8] data}
        i8_array_4 = ir.ArrayType(i8, 4)
        maybe_i32_type = ir.LiteralStructType([i32, i8_array_4])
        return_type = maybe_i32_type
        param_types = [string_type]
        call_args = [receiver_value]
    elif method == "to_i64":
        # to_i64() -> Maybe<i64> (enum struct)
        # Maybe<i64> layout: {i32 tag, [8 x i8] data}
        i8_array_8 = ir.ArrayType(i8, 8)
        maybe_i64_type = ir.LiteralStructType([i32, i8_array_8])
        return_type = maybe_i64_type
        param_types = [string_type]
        call_args = [receiver_value]
    elif method == "to_f64":
        # to_f64() -> Maybe<f64> (enum struct)
        # Maybe<f64> layout: {i32 tag, [8 x i8] data}
        i8_array_8 = ir.ArrayType(i8, 8)
        maybe_f64_type = ir.LiteralStructType([i32, i8_array_8])
        return_type = maybe_f64_type
        param_types = [string_type]
        call_args = [receiver_value]
    else:
        raise_internal_error("CE0077", method=method)

    # Declare the external function
    from sushi_lang.backend.functions import declare_stdlib_function
    stdlib_func = declare_stdlib_function(
        codegen.module,
        func_name,
        return_type,
        param_types
    )

    # Emit the call
    result = codegen.builder.call(
        stdlib_func,
        call_args,
        name=f"{method}_result"
    )

    # Special handling for methods that return array structs by value
    # The compiler expects pointers to array structs, so we need to allocate on stack
    if method in ("to_bytes", "split"):
        # Allocate struct on stack
        array_ptr = codegen.builder.alloca(return_type, name=f"{method}_array")
        # Store the result struct
        codegen.builder.store(result, array_ptr)
        # Return pointer to the stack-allocated struct
        return array_ptr

    # Convert to i1 if needed (for boolean methods used in conditions)
    if to_i1 and return_type == i8:
        result = codegen.utils.as_i1(result)

    return result
