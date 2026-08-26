"""Standard library string method call emission."""
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

    Every argument is emitted ONCE, here, through the built-in call-argument seam. Each
    arm below then picks the signature and reads `arg_values`; emitting inside an arm is
    what let an owning temporary reach a string method with no owner at all (#475).
    """
    from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg

    require_builder(codegen)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i8_ptr = ir.IntType(INT8_BIT_WIDTH).as_pointer()
    string_type = codegen.types.string_struct  # {i8* data, i32 size}

    func_name = f"string_{method}"
    arg_values = [emit_borrowed_arg(codegen, arg) for arg in args]

    if method in ("len", "size"):
        return_type = i32
        param_types = [string_type]
        call_args = [receiver_value]
    elif method == "concat":
        return_type = string_type
        param_types = [string_type, string_type]
        call_args = [receiver_value, *arg_values]
    elif method in ("contains", "starts_with", "ends_with"):
        return_type = i8
        param_types = [string_type, string_type]
        call_args = [receiver_value, *arg_values]
    elif method == "count":
        return_type = i32
        param_types = [string_type, string_type]
        call_args = [receiver_value, *arg_values]
    elif method in ("sleft", "sright", "char_at", "repeat"):
        return_type = string_type
        param_types = [string_type, i32]
        call_args = [receiver_value, *arg_values]
    elif method in ("s", "ss"):
        return_type = string_type
        param_types = [string_type, i32, i32]
        call_args = [receiver_value, *arg_values]
    elif method in ("upper", "lower", "cap", "trim", "tleft", "tright", "reverse"):
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
        call_args = [receiver_value, *arg_values]
    elif method == "join":
        # join(string[] parts) -> string
        # Array struct: {i32 len, i32 cap, string* data}
        # Takes array struct by value (loaded from stack pointer)
        return_type = string_type
        string_struct_ptr = string_type.as_pointer()
        array_struct_type = ir.LiteralStructType([i32, i32, string_struct_ptr])
        param_types = [string_type, array_struct_type]
        array_value = arg_values[0]
        if isinstance(array_value.type, ir.PointerType):
            array_value = codegen.builder.load(array_value, name="array_value")
        call_args = [receiver_value, array_value]
    elif method == "replace":
        return_type = string_type
        param_types = [string_type, string_type, string_type]
        call_args = [receiver_value, *arg_values]
    elif method == "pad_left":
        return_type = string_type
        param_types = [string_type, i32, string_type]
        call_args = [receiver_value, *arg_values]
    elif method == "pad_right":
        return_type = string_type
        param_types = [string_type, i32, string_type]
        call_args = [receiver_value, *arg_values]
    elif method == "strip_prefix":
        return_type = string_type
        param_types = [string_type, string_type]
        call_args = [receiver_value, *arg_values]
    elif method == "strip_suffix":
        return_type = string_type
        param_types = [string_type, string_type]
        call_args = [receiver_value, *arg_values]
    elif method == "find":
        # find(string needle) -> Maybe<i32> (enum struct)
        # Maybe<i32> layout (#300 phase 2): {i32 tag, [1 x i64] data}
        # tag = 0 for Some, tag = 1 for None
        from sushi_lang.sushi_stdlib.src.type_definitions import get_maybe_type
        return_type = get_maybe_type(i32)
        param_types = [string_type, string_type]
        call_args = [receiver_value, *arg_values]
    elif method == "find_last":
        # find_last(string needle) -> Maybe<i32> (enum struct)
        # Maybe<i32> layout (#300 phase 2): {i32 tag, [1 x i64] data}
        # tag = 0 for Some, tag = 1 for None
        from sushi_lang.sushi_stdlib.src.type_definitions import get_maybe_type
        return_type = get_maybe_type(i32)
        param_types = [string_type, string_type]
        call_args = [receiver_value, *arg_values]
    elif method == "to_i32":
        # to_i32() -> Maybe<i32> (enum struct)
        # Maybe<i32> layout (#300 phase 2): {i32 tag, [1 x i64] data}
        from sushi_lang.sushi_stdlib.src.type_definitions import get_maybe_type
        return_type = get_maybe_type(i32)
        param_types = [string_type]
        call_args = [receiver_value]
    elif method == "to_i64":
        # to_i64() -> Maybe<i64> (enum struct)
        # Maybe<i64> layout (#300 phase 2): {i32 tag, [1 x i64] data}
        from sushi_lang.sushi_stdlib.src.type_definitions import get_maybe_type
        return_type = get_maybe_type(ir.IntType(64))
        param_types = [string_type]
        call_args = [receiver_value]
    elif method == "to_f64":
        # to_f64() -> Maybe<f64> (enum struct)
        # Maybe<f64> layout (#300 phase 2): {i32 tag, [1 x i64] data}
        from sushi_lang.sushi_stdlib.src.type_definitions import get_maybe_type
        return_type = get_maybe_type(ir.DoubleType())
        param_types = [string_type]
        call_args = [receiver_value]
    else:
        raise_internal_error("CE0077", method=method)

    from sushi_lang.backend.functions import declare_stdlib_function
    stdlib_func = declare_stdlib_function(
        codegen.module,
        func_name,
        return_type,
        param_types
    )

    result = codegen.builder.call(
        stdlib_func,
        call_args,
        name=f"{method}_result"
    )

    if method in ("to_bytes", "split"):
        array_ptr = codegen.builder.alloca(return_type, name=f"{method}_array")
        codegen.builder.store(result, array_ptr)
        return array_ptr

    if to_i1 and return_type == i8:
        result = codegen.utils.as_i1(result)

    return result
