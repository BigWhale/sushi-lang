"""Result construction for the <io/files> generators.

The byte layout of a Result<T, FileError> is built here and nowhere else:
{i32 tag, [K x i64] data}, with the payload memcpy'd into the data array.
The call-site declarations in the backend use the same get_result_type
helper, which is what keeps the .bc and the program byte-matched.
"""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types


def emit_ok_result(builder: ir.IRBuilder, result_type: ir.LiteralStructType,
                   value: ir.Value, size_bytes: int) -> ir.Value:
    """Build Result.Ok(value): the value's size_bytes land at the payload start."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    data_array_type = result_type.elements[1]
    memcpy_fn = builder.module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])

    value_alloca = builder.alloca(value.type, name="ok_value")
    builder.store(value, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="ok_data")
    builder.store(ir.Constant(data_array_type, None), data_alloca)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [
        builder.bitcast(data_alloca, i8_ptr),
        builder.bitcast(value_alloca, i8_ptr),
        ir.Constant(i64, size_bytes), is_volatile,
    ])
    data_value = builder.load(data_alloca, name="ok_data_value")

    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ir.Constant(i32, 0), 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    return ok_result


def emit_err_result(builder: ir.IRBuilder, result_type: ir.LiteralStructType,
                    error_tag: ir.Value) -> ir.Value:
    """Build Result.Err(FileError) with the given variant tag in the payload.

    A FileError value is {i32 tag, [1 x i64] zeros}; inside the Result data
    array its tag sits in the first four bytes.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    data_array_type = result_type.elements[1]

    data_alloca = builder.alloca(data_array_type, name="err_data")
    builder.store(ir.Constant(data_array_type, None), data_alloca)
    tag_ptr = builder.bitcast(data_alloca, i32.as_pointer(), name="err_tag_ptr")
    builder.store(error_tag, tag_ptr)
    data_value = builder.load(data_alloca, name="err_data_value")

    err_result = ir.Constant(result_type, ir.Undefined)
    err_result = builder.insert_value(err_result, ir.Constant(i32, 1), 0, name="err_with_tag")
    err_result = builder.insert_value(err_result, data_value, 1, name="err_result")
    return err_result
