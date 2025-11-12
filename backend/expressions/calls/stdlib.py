"""
Standard library function call emission (stdio, file, string, primitive, time).

This module handles external calls to precompiled stdlib functions
for I/O operations, string methods, primitive conversions, and time functions.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from backend.llvm_constants import FALSE_I1
from internals.errors import raise_internal_error
from semantics.typesys import BuiltinType

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


def emit_stdlib_stdio_call(
    codegen: 'LLVMCodegen',
    stream_name: str,
    method: str,
    args: list,
    to_i1: bool
) -> ir.Value:
    """Emit a call to a stdlib stdio method.

    This function emits an external call to a precompiled stdlib function
    for stdio operations (stdin, stdout, stderr).

    Args:
        codegen: The LLVM code generator
        stream_name: The stream name ('stdin', 'stdout', or 'stderr')
        method: The method name (e.g., "readln", "write", "read_bytes", "write_bytes")
        args: The method arguments
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the method is not implemented in stdlib
    """
    if codegen.builder is None:
        raise_internal_error("CE0009")
    # Map method names to stdlib function names
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8_ptr = i8.as_pointer()

    # Build function name: sushi_{stream}_{method}
    func_name = f"sushi_{stream_name}_{method}"

    from backend.llvm_functions import declare_stdlib_function

    # Handle each method type
    if stream_name == "stdin":
        if method == "readln":
            # {ptr, i32} @sushi_stdin_readln()
            # Returns fat pointer struct {i8*, i32}
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, string_struct_ty, [])
            return codegen.builder.call(stdlib_func, [], name="stdin_readln_result")

        elif method == "read":
            # {ptr, i32} @sushi_stdin_read()
            # Returns fat pointer struct {i8*, i32}
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, string_struct_ty, [])
            return codegen.builder.call(stdlib_func, [], name="stdin_read_result")

        elif method == "read_bytes":
            # {i32, i32, ptr} @sushi_stdin_read_bytes(i32 %count)
            # Returns array struct by value
            array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
            arg_value = codegen.expressions.emit_expr(args[0])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, array_struct_ty, [i32])
            result = codegen.builder.call(stdlib_func, [arg_value], name="stdin_read_bytes_result")

            # Store result in a slot and return pointer (to match variable storage conventions)
            result_slot = codegen.builder.alloca(array_struct_ty, name="stdin_read_bytes_slot")
            codegen.builder.store(result, result_slot)
            return result_slot

        elif method == "lines":
            # {i32, i32, {i8*, i32}*} @sushi_stdin_lines()
            # Returns iterator struct with fat pointer element type
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
            iterator_struct_ty = ir.LiteralStructType([i32, i32, string_struct_ty.as_pointer()])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, iterator_struct_ty, [])
            return codegen.builder.call(stdlib_func, [], name="stdin_lines_result")

    elif stream_name in ["stdout", "stderr"]:
        if method == "write":
            # i32 @sushi_stdout_write({ptr, i32} %str)
            # Accepts fat pointer struct {i8*, i32}
            string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
            arg_value = codegen.expressions.emit_expr(args[0])
            stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [string_struct_ty])
            return codegen.builder.call(stdlib_func, [arg_value], name=f"{stream_name}_write_result")

        elif method == "write_bytes":
            # i32 @sushi_stdout_write_bytes({i32, i32, ptr} %array)
            # Accepts array struct by value
            array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
            arg_value = codegen.expressions.emit_expr(args[0])

            # arg_value is already the struct by value (from emit_name loading the variable)
            # No additional load needed!

            stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [array_struct_ty])
            return codegen.builder.call(stdlib_func, [arg_value], name=f"{stream_name}_write_bytes_result")

    raise_internal_error("CE0028", method=method)


def emit_stdlib_file_call(
    codegen: 'LLVMCodegen',
    file_ptr: ir.Value,
    method: str,
    args: list,
    to_i1: bool
) -> ir.Value:
    """Emit a call to a stdlib file method.

    This function emits an external call to a precompiled stdlib function
    for file operations.

    Args:
        codegen: The LLVM code generator
        file_ptr: The FILE* pointer value
        method: The method name (e.g., "read", "write", "seek")
        args: The method arguments
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the method is not implemented in stdlib
    """
    if codegen.builder is None:
        raise_internal_error("CE0009")
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i64 = ir.IntType(INT64_BIT_WIDTH)
    i8_ptr = i8.as_pointer()

    # Build function name: sushi_file_{method}
    func_name = f"sushi_file_{method}"

    from backend.llvm_functions import declare_stdlib_function

    # Handle each method type
    if method in ("read", "readln", "readch"):
        # {ptr, i32} @sushi_file_{method}(ptr %file_ptr)
        # Returns fat pointer struct {i8*, i32}
        string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
        stdlib_func = declare_stdlib_function(codegen.module, func_name, string_struct_ty, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name=f"file_{method}_result")
        return result

    elif method == "lines":
        # {i32, i32, {i8*, i32}*} @sushi_file_lines(ptr %file_ptr)
        # Returns iterator struct with fat pointer element type
        string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
        iterator_struct_ty = ir.LiteralStructType([i32, i32, string_struct_ty.as_pointer()])
        stdlib_func = declare_stdlib_function(codegen.module, func_name, iterator_struct_ty, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_lines_result")
        return result

    elif method in ("write", "writeln"):
        # i32 @sushi_file_{method}(ptr %file_ptr, {ptr, i32} %string)
        # Accepts fat pointer struct {i8*, i32}
        string_struct_ty = ir.LiteralStructType([i8_ptr, i32])
        arg_value = codegen.expressions.emit_expr(args[0])
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr, string_struct_ty])
        result = codegen.builder.call(stdlib_func, [file_ptr, arg_value], name=f"file_{method}_result")
        return result

    elif method == "read_bytes":
        # {i32, i32, i8*} @sushi_file_read_bytes(ptr %file_ptr, i32 %count)
        # Returns array struct by value
        array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
        arg_value = codegen.expressions.emit_expr(args[0])
        stdlib_func = declare_stdlib_function(codegen.module, func_name, array_struct_ty, [i8_ptr, i32])
        result = codegen.builder.call(stdlib_func, [file_ptr, arg_value], name="file_read_bytes_result")

        # Store result in a slot and return pointer (to match variable storage conventions)
        result_slot = codegen.builder.alloca(array_struct_ty, name="read_bytes_slot")
        codegen.builder.store(result, result_slot)
        return result_slot

    elif method == "write_bytes":
        # i32 @sushi_file_write_bytes(ptr %file_ptr, {i32, i32, i8*} %array_struct)
        # Accepts array struct by value
        array_struct_ty = ir.LiteralStructType([i32, i32, i8_ptr])
        arg_value = codegen.expressions.emit_expr(args[0])

        # arg_value is already the struct by value (from emit_name loading the variable)
        # No additional load needed!

        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr, array_struct_ty])
        result = codegen.builder.call(stdlib_func, [file_ptr, arg_value], name="file_write_bytes_result")
        return result

    elif method == "seek":
        # i32 @sushi_file_seek(ptr %file_ptr, i64 %offset, ptr %seekfrom)
        offset_value = codegen.expressions.emit_expr(args[0])
        seekfrom_value = codegen.expressions.emit_expr(args[1])

        # SeekFrom enum struct type: {i32 tag, [0 x i8] data}
        seekfrom_struct_ty = ir.LiteralStructType([i32, ir.ArrayType(i8, 0)])

        # seekfrom_value is the enum by value (from emit_name loading it)
        # Stdlib expects a pointer, so store it in a slot
        seekfrom_slot = codegen.builder.alloca(seekfrom_struct_ty, name="seekfrom_slot")
        codegen.builder.store(seekfrom_value, seekfrom_slot)

        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr, i64, seekfrom_struct_ty.as_pointer()])
        result = codegen.builder.call(stdlib_func, [file_ptr, offset_value, seekfrom_slot], name="file_seek_result")
        return result

    elif method == "tell":
        # i64 @sushi_file_tell(ptr %file_ptr)
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i64, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_tell_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif method == "close":
        # i32 @sushi_file_close(ptr %file_ptr)
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_close_result")
        return result

    elif method == "is_open":
        # i32 @sushi_file_is_open(ptr %file_ptr)
        stdlib_func = declare_stdlib_function(codegen.module, func_name, i32, [i8_ptr])
        result = codegen.builder.call(stdlib_func, [file_ptr], name="file_is_open_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    raise_internal_error("CE0028", method=method)


def emit_stdlib_primitive_call(
    codegen: 'LLVMCodegen',
    method: str,
    receiver_value: ir.Value,
    receiver_type: ir.Type,
    semantic_type_str: str
) -> ir.Value:
    """Emit a call to a stdlib primitive method.

    This function emits an external call to a precompiled stdlib function
    instead of emitting inline IR. Used when the appropriate stdlib module
    is imported via use <module> syntax.

    Args:
        codegen: The LLVM code generator
        method: The method name (e.g., "to_str")
        receiver_value: The LLVM value of the receiver
        receiver_type: The LLVM type of the receiver
        semantic_type_str: String representation of semantic type (e.g., "i32")

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the method is not implemented in stdlib
    """
    if codegen.builder is None:
        raise_internal_error("CE0009")
    # For now, only handle to_str()
    if method != "to_str":
        raise_internal_error("CE0028", method=method)

    # Build function name: sushi_{type}_to_str
    func_name = f"sushi_{semantic_type_str}_to_str"

    # Return type is always string fat pointer struct {i8*, i32} for to_str()
    string_struct_type = codegen.types.string_struct

    # Declare the external function
    from backend.llvm_functions import declare_stdlib_function
    stdlib_func = declare_stdlib_function(
        codegen.module,
        func_name,
        string_struct_type,
        [receiver_type]
    )

    # Emit the call
    result = codegen.builder.call(
        stdlib_func,
        [receiver_value],
        name=f"{semantic_type_str}_to_str_result"
    )

    return result


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
    if codegen.builder is None:
        raise_internal_error("CE0009")
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
    elif method in ("sleft", "sright", "char_at"):
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
    elif method in ("upper", "lower", "cap", "trim", "tleft", "tright"):
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
    elif method == "replace":
        # replace(string old, string new) -> string
        return_type = string_type
        param_types = [string_type, string_type, string_type]
        old_value = codegen.expressions.emit_expr(args[0])
        new_value = codegen.expressions.emit_expr(args[1])
        call_args = [receiver_value, old_value, new_value]
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
    from backend.llvm_functions import declare_stdlib_function
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


def emit_math_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a math module function.

    This function emits an external call to a precompiled stdlib math function.
    Maps user-facing function names (abs, min, max, sqrt, etc.) to their internal
    sushi_* prefixed names in the stdlib.

    Args:
        codegen: The LLVM code generator
        func_name: The function name ('abs', 'min', 'max', 'sqrt', 'pow', etc.)
        expr: The function call expression
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the function is not a recognized math function
    """
    if codegen.builder is None:
        raise_internal_error("CE0009")

    from backend.llvm_functions import declare_stdlib_function

    # Get argument values
    args = [codegen.expressions.emit_expr(arg) for arg in expr.args]

    # Determine the type-specific function name for polymorphic functions (abs, min, max)
    if func_name in {'abs', 'min', 'max'} and args:
        # These are polymorphic - need to determine the type suffix
        arg_type = args[0].type
        type_suffix = _get_math_type_suffix(arg_type)
        stdlib_func_name = f"sushi_{func_name}_{type_suffix}"

        # Declare the function with the appropriate signature
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, arg_type, [arg_type] * len(args))
    elif func_name in {'sqrt', 'floor', 'ceil', 'round', 'trunc'}:
        # These take f64 and return f64
        f64 = ir.DoubleType()
        stdlib_func_name = f"sushi_{func_name}"
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, f64, [f64])
        # Convert args to f64 if needed
        if args and args[0].type != f64:
            from backend.expressions.casts import cast_int_to_float, cast_float_to_float
            if isinstance(args[0].type, ir.IntType):
                args[0] = cast_int_to_float(codegen, args[0], f64)
            else:
                args[0] = cast_float_to_float(codegen, args[0], f64)
    elif func_name == 'pow':
        # pow takes two f64 arguments and returns f64
        f64 = ir.DoubleType()
        stdlib_func_name = f"sushi_{func_name}"
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, f64, [f64, f64])
        # Convert both args to f64 if needed
        from backend.expressions.casts import cast_int_to_float, cast_float_to_float
        for i in range(len(args)):
            if args[i].type != f64:
                if isinstance(args[i].type, ir.IntType):
                    args[i] = cast_int_to_float(codegen, args[i], f64)
                else:
                    args[i] = cast_float_to_float(codegen, args[i], f64)
    else:
        raise ValueError(f"Unknown math function: {func_name}")

    # Call the function
    result = codegen.builder.call(stdlib_func, args, name=f"{func_name}_result")
    return codegen.utils.as_i1(result) if to_i1 else result


def _get_math_type_suffix(llvm_type) -> str:
    """Get the type suffix for polymorphic math functions.

    Args:
        llvm_type: The LLVM IR type

    Returns:
        Type suffix string (e.g., 'i32', 'f64', 'u8')

    Note:
        We cannot distinguish between signed and unsigned integers in LLVM IR alone.
        Both i32 and u32 are represented as ir.IntType(32).
        For now, we default to signed types. If we need unsigned support for min/max,
        we would need to pass semantic type information through.
    """
    from llvmlite import ir

    if isinstance(llvm_type, ir.IntType):
        bit_width = llvm_type.width
        if bit_width == 8:
            return 'i8'
        elif bit_width == 16:
            return 'i16'
        elif bit_width == 32:
            return 'i32'
        elif bit_width == 64:
            return 'i64'
    elif isinstance(llvm_type, ir.FloatType):
        return 'f32'
    elif isinstance(llvm_type, ir.DoubleType):
        return 'f64'

    raise ValueError(f"Unsupported type for math function: {llvm_type}")


def emit_time_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a time module function.

    This function emits an external call to a precompiled stdlib time function.
    Maps user-facing function names (sleep, msleep, etc.) to their internal
    sushi_* prefixed names in the stdlib.

    Args:
        codegen: The LLVM code generator
        func_name: The function name ('sleep', 'msleep', 'usleep', or 'nanosleep')
        expr: The function call expression
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call (Result<i32> enum)

    Raises:
        ValueError: If the function is not a recognized time function
    """
    if codegen.builder is None:
        raise_internal_error("CE0009")

    i32 = ir.IntType(INT32_BIT_WIDTH)
    i64 = ir.IntType(INT64_BIT_WIDTH)

    # Map user function name to stdlib function name
    stdlib_func_name = f"sushi_{func_name}"

    from backend.llvm_functions import declare_stdlib_function

    # All time functions return i32 (0 on success, remaining microseconds if interrupted)
    # But they're wrapped in Result<i32> at the semantic level
    # The actual LLVM functions return bare i32

    if func_name in ["sleep", "msleep", "usleep"]:
        # These functions take one i64 argument
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))

        arg_value = codegen.expressions.emit_expr(expr.args[0])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [i64])
        result = codegen.builder.call(stdlib_func, [arg_value], name=f"{func_name}_result")

    elif func_name == "nanosleep":
        # nanosleep takes two i64 arguments (seconds, nanoseconds)
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method="nanosleep", expected=2, got=len(expr.args))

        seconds_value = codegen.expressions.emit_expr(expr.args[0])
        nanoseconds_value = codegen.expressions.emit_expr(expr.args[1])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [i64, i64])
        result = codegen.builder.call(stdlib_func, [seconds_value, nanoseconds_value], name="nanosleep_result")

    else:
        raise_internal_error("CE0024", type="time", method=func_name)

    # The stdlib functions return bare i32, but Sushi functions return Result<i32>
    # We need to wrap the result in a Result.Ok() enum
    # Result<i32> enum layout: {i32 tag, [N x i8] data}

    from semantics.typesys import ResultType, BuiltinType
    result_type = ResultType(ok_type=BuiltinType.I32)
    result_llvm_type = codegen.types.ll_type(result_type)

    # Get the Result<i32> enum from the enum table
    result_enum_name = "Result<i32>"
    if result_enum_name in codegen.enum_table.by_name:
        result_enum = codegen.enum_table.by_name[result_enum_name]
        ok_variant_index = result_enum.get_variant_index("Ok")

        # Create Result.Ok(value) enum
        ok_result = ir.Constant(result_llvm_type, ir.Undefined)
        tag = ir.Constant(codegen.types.i32, ok_variant_index)
        ok_result = codegen.builder.insert_value(ok_result, tag, 0, name="ok_tag")

        # Pack the i32 value into the data array
        # Get the data array type from the enum type
        data_array_type = result_llvm_type.elements[1]

        # Allocate space for the value and data array
        value_alloca = codegen.builder.alloca(i32, name="time_result_value")
        codegen.builder.store(result, value_alloca)

        data_alloca = codegen.builder.alloca(data_array_type, name="data_array")

        # Bitcast pointers to i8* for memcpy
        src_ptr = codegen.builder.bitcast(value_alloca, codegen.types.i8.as_pointer())
        dest_ptr = codegen.builder.bitcast(data_alloca, codegen.types.i8.as_pointer())

        # Copy i32 value into data array (4 bytes)
        size_const = ir.Constant(codegen.types.i32, 4)
        memcpy_fn = codegen.module.declare_intrinsic('llvm.memcpy', [
            ir.PointerType(codegen.types.i8),
            ir.PointerType(codegen.types.i8),
            codegen.types.i32
        ])
        is_volatile = FALSE_I1
        codegen.builder.call(memcpy_fn, [dest_ptr, src_ptr, size_const, is_volatile])

        # Load the data array and insert into enum
        data_value = codegen.builder.load(data_alloca, name="data_value")
        ok_result = codegen.builder.insert_value(ok_result, data_value, 1, name="ok_result")

        return codegen.utils.as_i1(ok_result) if to_i1 else ok_result
    else:
        raise_internal_error("CE0091", type="Result<i32>")


def emit_env_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a sys/env module function.

    This function emits an external call to a precompiled stdlib env function.
    Maps user-facing function names (getenv, setenv) to their internal
    sushi_* prefixed names in the stdlib.

    Args:
        codegen: The LLVM code generator
        func_name: The function name ('getenv' or 'setenv')
        expr: The function call expression
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call (Maybe<string> or Result<i32>)

    Raises:
        ValueError: If the function is not a recognized env function
    """
    if codegen.builder is None:
        raise_internal_error("CE0009")

    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8_ptr = ir.IntType(8).as_pointer()

    # Map user function name to stdlib function name
    stdlib_func_name = f"sushi_{func_name}"

    from backend.llvm_functions import declare_stdlib_function

    # String type: {i8* data, i32 size}
    string_type = codegen.types.ll_type(BuiltinType.STRING)

    if func_name == "getenv":
        # getenv(string key) -> Maybe<string>
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method="getenv", expected=1, got=len(expr.args))

        key_value = codegen.expressions.emit_expr(expr.args[0])

        # Maybe<string> type: {i32 tag, [12 x i8] data}
        maybe_string_data_size = 12
        maybe_string_type = ir.LiteralStructType([i32, ir.ArrayType(ir.IntType(8), maybe_string_data_size)])

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, maybe_string_type, [string_type])
        result = codegen.builder.call(stdlib_func, [key_value], name="getenv_result")

        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "setenv":
        # setenv(string key, string value) -> Result<i32>
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method="setenv", expected=2, got=len(expr.args))

        key_value = codegen.expressions.emit_expr(expr.args[0])
        value_value = codegen.expressions.emit_expr(expr.args[1])

        # The stdlib function returns bare i32
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [string_type, string_type])
        result = codegen.builder.call(stdlib_func, [key_value, value_value], name="setenv_result")

        # Wrap in Result.Ok() enum (same as time functions)
        from semantics.typesys import ResultType
        result_type = ResultType(ok_type=BuiltinType.I32)
        result_llvm_type = codegen.types.ll_type(result_type)

        # Get the Result<i32> enum from the enum table
        result_enum_name = "Result<i32>"
        if result_enum_name in codegen.enum_table.by_name:
            result_enum = codegen.enum_table.by_name[result_enum_name]
            ok_variant_index = result_enum.get_variant_index("Ok")

            # Create Result.Ok(value) enum
            ok_result = ir.Constant(result_llvm_type, ir.Undefined)
            tag = ir.Constant(codegen.types.i32, ok_variant_index)
            ok_result = codegen.builder.insert_value(ok_result, tag, 0, name="ok_tag")

            # Pack the i32 value into the data array
            data_array_type = result_llvm_type.elements[1]

            # Allocate space for the value and data array
            value_alloca = codegen.builder.alloca(i32, name="setenv_result_value")
            codegen.builder.store(result, value_alloca)

            data_alloca = codegen.builder.alloca(data_array_type, name="data_array")

            # Bitcast pointers to i8* for memcpy
            src_ptr = codegen.builder.bitcast(value_alloca, codegen.types.i8.as_pointer())
            dest_ptr = codegen.builder.bitcast(data_alloca, codegen.types.i8.as_pointer())

            # Copy i32 value into data array (4 bytes)
            size_const = ir.Constant(codegen.types.i32, 4)
            memcpy_fn = codegen.module.declare_intrinsic('llvm.memcpy', [
                ir.PointerType(codegen.types.i8),
                ir.PointerType(codegen.types.i8),
                codegen.types.i32
            ])
            is_volatile = FALSE_I1
            codegen.builder.call(memcpy_fn, [dest_ptr, src_ptr, size_const, is_volatile])

            # Load the data array and insert into enum
            data_value = codegen.builder.load(data_alloca, name="data_value")
            ok_result = codegen.builder.insert_value(ok_result, data_value, 1, name="ok_result")

            return codegen.utils.as_i1(ok_result) if to_i1 else ok_result
        else:
            raise_internal_error("CE0091", type="Result<i32>")

    else:
        raise_internal_error("CE0024", type="sys/env", method=func_name)


def emit_random_function(codegen: 'LLVMCodegen', expr, func_name: str, to_i1: bool) -> ir.Value:
    """Emit a call to a random module function.

    This function emits an external call to a precompiled stdlib random function.
    Maps user-facing function names (rand, rand_range, srand, rand_f64) to their
    internal sushi_* prefixed names in the stdlib.

    Args:
        codegen: The LLVM code generator
        func_name: The function name ('rand', 'rand_range', 'srand', or 'rand_f64')
        expr: The function call expression
        to_i1: Whether to convert result to i1 (for boolean conditions)

    Returns:
        The result of the stdlib function call

    Raises:
        ValueError: If the function is not a recognized random function
    """
    if codegen.builder is None:
        raise_internal_error("CE0009")

    i32 = ir.IntType(INT32_BIT_WIDTH)
    i64 = ir.IntType(INT64_BIT_WIDTH)
    f64 = ir.DoubleType()
    void = ir.VoidType()

    # Map user function name to stdlib function name
    stdlib_func_name = f"sushi_{func_name}"

    from backend.llvm_functions import declare_stdlib_function

    # Random functions return bare types (not Result<T>)
    # Result wrapping happens at semantic level

    if func_name == "rand":
        # rand() -> u64 (no parameters)
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method=func_name, expected=0, got=len(expr.args))

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i64, [])
        result = codegen.builder.call(stdlib_func, [], name="rand_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "rand_range":
        # rand_range(i32 min, i32 max) -> i32
        if len(expr.args) != 2:
            raise_internal_error("CE0023", method=func_name, expected=2, got=len(expr.args))

        min_value = codegen.expressions.emit_expr(expr.args[0])
        max_value = codegen.expressions.emit_expr(expr.args[1])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, i32, [i32, i32])
        result = codegen.builder.call(stdlib_func, [min_value, max_value], name="rand_range_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    elif func_name == "srand":
        # srand(u64 seed) -> ~ (void)
        if len(expr.args) != 1:
            raise_internal_error("CE0023", method=func_name, expected=1, got=len(expr.args))

        seed_value = codegen.expressions.emit_expr(expr.args[0])
        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, void, [i64])
        codegen.builder.call(stdlib_func, [seed_value])
        # Return blank (undef i32 value)
        return ir.Constant(i32, ir.Undefined)

    elif func_name == "rand_f64":
        # rand_f64() -> f64 (no parameters)
        if len(expr.args) != 0:
            raise_internal_error("CE0023", method=func_name, expected=0, got=len(expr.args))

        stdlib_func = declare_stdlib_function(codegen.module, stdlib_func_name, f64, [])
        result = codegen.builder.call(stdlib_func, [], name="rand_f64_result")
        return codegen.utils.as_i1(result) if to_i1 else result

    else:
        raise_internal_error("CE0024", type="random", method=func_name)
