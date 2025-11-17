"""
Result Wrapping Helpers

Reusable patterns for constructing Result<T> enum values in stdlib functions.
Eliminates duplication of Result construction code.
"""
from llvmlite import ir


def wrap_in_result_ok(builder: ir.IRBuilder, i8: ir.IntType, value: ir.Value) -> ir.Value:
    """Wrap a value in Result.Ok variant.

    Args:
        builder: IR builder
        i8: i8 type
        value: Value to wrap

    Returns:
        Result<T> struct: { i8 tag=0, T value }
    """
    ok_tag = ir.Constant(i8, 0)
    result_type = ir.LiteralStructType([i8, value.type])
    return ir.Constant.literal_struct([ok_tag, value])


def wrap_in_result_err(builder: ir.IRBuilder, i8: ir.IntType, value_type: ir.Type) -> ir.Value:
    """Wrap in Result.Err variant with zero value.

    Args:
        builder: IR builder
        i8: i8 type
        value_type: Type of the Ok variant (for struct sizing)

    Returns:
        Result<T> struct: { i8 tag=1, T zero_value }
    """
    err_tag = ir.Constant(i8, 1)
    zero_value = ir.Constant(value_type, 0) if isinstance(value_type, ir.IntType) else ir.Constant(value_type, ir.Undefined)
    result_type = ir.LiteralStructType([i8, value_type])
    return ir.Constant.literal_struct([err_tag, zero_value])


def build_result_conditional(
    func: ir.Function,
    builder: ir.IRBuilder,
    condition: ir.Value,
    ok_value: ir.Value,
    i8: ir.IntType
) -> ir.Value:
    """Build conditional Result: if (condition) Ok(value) else Err().

    This is a specialized pattern for functions that return Result<T> based
    on a boolean condition (e.g., syscall success/failure).

    Args:
        func: Function to add blocks to
        builder: IR builder
        condition: Boolean condition (i1 or i8)
        ok_value: Value to return on success
        i8: i8 type

    Returns:
        Result<T> value (builder positioned after merge)
    """
    from stdlib.src.ir_builders import IRConditionalBuilder

    result_type = ir.LiteralStructType([i8, ok_value.type])
    result_ptr = builder.alloca(result_type, name="result_ptr")

    def then_fn(then_builder: ir.IRBuilder):
        ok_result = wrap_in_result_ok(then_builder, i8, ok_value)
        then_builder.store(ok_result, result_ptr)

    def else_fn(else_builder: ir.IRBuilder):
        err_result = wrap_in_result_err(else_builder, i8, ok_value.type)
        else_builder.store(err_result, result_ptr)

    merge_block = IRConditionalBuilder.build_simple_conditional(
        func, builder, condition, then_fn, else_fn
    )

    builder = ir.IRBuilder(merge_block)
    return builder.load(result_ptr, name="result")
