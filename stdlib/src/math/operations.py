"""
Math operation implementations for Sushi math module.

Implements mathematical functions:
- abs: Absolute value for signed numeric types
- min/max: Minimum and maximum for all numeric types
- sqrt: Square root using LLVM intrinsic
- pow: Power function using LLVM intrinsic
- floor/ceil/round/trunc: Rounding functions using LLVM intrinsics

All functions use LLVM intrinsics where available for optimal performance.
"""
from __future__ import annotations
from llvmlite import ir


def generate_abs_functions(module: ir.Module) -> None:
    """Generate abs() functions for all signed numeric types.

    Generates:
        sushi_abs_i8(i8) -> i8
        sushi_abs_i16(i16) -> i16
        sushi_abs_i32(i32) -> i32
        sushi_abs_i64(i64) -> i64
        sushi_abs_f32(f32) -> f32
        sushi_abs_f64(f64) -> f64

    Implementation for integers:
        result = (x < 0) ? -x : x

    Implementation for floats:
        Use LLVM's fabs intrinsic
    """
    # Integer types
    int_types = [
        (ir.IntType(8), 'i8'),
        (ir.IntType(16), 'i16'),
        (ir.IntType(32), 'i32'),
        (ir.IntType(64), 'i64'),
    ]

    for int_type, type_name in int_types:
        func_type = ir.FunctionType(int_type, [int_type])
        func = ir.Function(module, func_type, name=f"sushi_abs_{type_name}")

        value_param = func.args[0]
        value_param.name = "value"

        entry = func.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        # Check if value < 0
        zero = ir.Constant(int_type, 0)
        is_negative = builder.icmp_signed('<', value_param, zero, name="is_negative")

        # Create blocks
        negative_block = func.append_basic_block("negative")
        positive_block = func.append_basic_block("positive")

        builder.cbranch(is_negative, negative_block, positive_block)

        # Negative block: return -value
        builder.position_at_end(negative_block)
        negated = builder.sub(zero, value_param, name="negated")
        builder.ret(negated)

        # Positive block: return value
        builder.position_at_end(positive_block)
        builder.ret(value_param)

    # Float types - use LLVM intrinsic
    float_types = [
        (ir.FloatType(), 'f32'),
        (ir.DoubleType(), 'f64'),
    ]

    for float_type, type_name in float_types:
        # Declare LLVM fabs intrinsic
        intrinsic_name = f"llvm.fabs.{type_name}"
        intrinsic_type = ir.FunctionType(float_type, [float_type])
        intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

        # Create our wrapper function
        func_type = ir.FunctionType(float_type, [float_type])
        func = ir.Function(module, func_type, name=f"sushi_abs_{type_name}")

        value_param = func.args[0]
        value_param.name = "value"

        entry = func.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        # Call LLVM intrinsic
        result = builder.call(intrinsic, [value_param])
        builder.ret(result)


def generate_min_max_functions(module: ir.Module) -> None:
    """Generate min() and max() functions for all numeric types.

    Generates:
        sushi_min_{type}(T, T) -> T
        sushi_max_{type}(T, T) -> T

    For all types: i8, i16, i32, i64, u8, u16, u32, u64, f32, f64

    Implementation:
        min: (a < b) ? a : b
        max: (a > b) ? a : b
    """
    # Signed integer types
    signed_int_types = [
        (ir.IntType(8), 'i8', True),
        (ir.IntType(16), 'i16', True),
        (ir.IntType(32), 'i32', True),
        (ir.IntType(64), 'i64', True),
    ]

    # Unsigned integer types
    unsigned_int_types = [
        (ir.IntType(8), 'u8', False),
        (ir.IntType(16), 'u16', False),
        (ir.IntType(32), 'u32', False),
        (ir.IntType(64), 'u64', False),
    ]

    # Float types
    float_types = [
        (ir.FloatType(), 'f32'),
        (ir.DoubleType(), 'f64'),
    ]

    # Generate for signed integers
    for int_type, type_name, is_signed in signed_int_types + unsigned_int_types:
        # Min function
        func_type = ir.FunctionType(int_type, [int_type, int_type])
        min_func = ir.Function(module, func_type, name=f"sushi_min_{type_name}")

        a_param = min_func.args[0]
        b_param = min_func.args[1]
        a_param.name = "a"
        b_param.name = "b"

        entry = min_func.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        # Compare: a < b
        if is_signed:
            cmp = builder.icmp_signed('<', a_param, b_param, name="cmp")
        else:
            cmp = builder.icmp_unsigned('<', a_param, b_param, name="cmp")

        result = builder.select(cmp, a_param, b_param, name="min")
        builder.ret(result)

        # Max function
        max_func = ir.Function(module, func_type, name=f"sushi_max_{type_name}")

        a_param = max_func.args[0]
        b_param = max_func.args[1]
        a_param.name = "a"
        b_param.name = "b"

        entry = max_func.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        # Compare: a > b
        if is_signed:
            cmp = builder.icmp_signed('>', a_param, b_param, name="cmp")
        else:
            cmp = builder.icmp_unsigned('>', a_param, b_param, name="cmp")

        result = builder.select(cmp, a_param, b_param, name="max")
        builder.ret(result)

    # Generate for floats
    for float_type, type_name in float_types:
        # Min function - use LLVM minnum intrinsic (handles NaN correctly)
        intrinsic_name = f"llvm.minnum.{type_name}"
        intrinsic_type = ir.FunctionType(float_type, [float_type, float_type])
        min_intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

        func_type = ir.FunctionType(float_type, [float_type, float_type])
        min_func = ir.Function(module, func_type, name=f"sushi_min_{type_name}")

        a_param = min_func.args[0]
        b_param = min_func.args[1]
        a_param.name = "a"
        b_param.name = "b"

        entry = min_func.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        result = builder.call(min_intrinsic, [a_param, b_param])
        builder.ret(result)

        # Max function - use LLVM maxnum intrinsic (handles NaN correctly)
        intrinsic_name = f"llvm.maxnum.{type_name}"
        max_intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

        max_func = ir.Function(module, func_type, name=f"sushi_max_{type_name}")

        a_param = max_func.args[0]
        b_param = max_func.args[1]
        a_param.name = "a"
        b_param.name = "b"

        entry = max_func.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        result = builder.call(max_intrinsic, [a_param, b_param])
        builder.ret(result)


def generate_sqrt(module: ir.Module) -> None:
    """Generate sqrt function: sqrt(f64) -> f64

    Uses LLVM's sqrt intrinsic for optimal performance.
    """
    f64 = ir.DoubleType()

    # Declare LLVM sqrt intrinsic
    intrinsic_name = "llvm.sqrt.f64"
    intrinsic_type = ir.FunctionType(f64, [f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    # Create our wrapper function
    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_sqrt")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call LLVM intrinsic
    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


def generate_pow(module: ir.Module) -> None:
    """Generate pow function: pow(f64, f64) -> f64

    Uses LLVM's pow intrinsic for optimal performance.
    """
    f64 = ir.DoubleType()

    # Declare LLVM pow intrinsic
    intrinsic_name = "llvm.pow.f64"
    intrinsic_type = ir.FunctionType(f64, [f64, f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    # Create our wrapper function
    func_type = ir.FunctionType(f64, [f64, f64])
    func = ir.Function(module, func_type, name="sushi_pow")

    base_param = func.args[0]
    exp_param = func.args[1]
    base_param.name = "base"
    exp_param.name = "exponent"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call LLVM intrinsic
    result = builder.call(intrinsic, [base_param, exp_param])
    builder.ret(result)


def generate_floor(module: ir.Module) -> None:
    """Generate floor function: floor(f64) -> f64

    Uses LLVM's floor intrinsic.
    """
    f64 = ir.DoubleType()

    # Declare LLVM floor intrinsic
    intrinsic_name = "llvm.floor.f64"
    intrinsic_type = ir.FunctionType(f64, [f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    # Create our wrapper function
    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_floor")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call LLVM intrinsic
    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


def generate_ceil(module: ir.Module) -> None:
    """Generate ceil function: ceil(f64) -> f64

    Uses LLVM's ceil intrinsic.
    """
    f64 = ir.DoubleType()

    # Declare LLVM ceil intrinsic
    intrinsic_name = "llvm.ceil.f64"
    intrinsic_type = ir.FunctionType(f64, [f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    # Create our wrapper function
    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_ceil")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call LLVM intrinsic
    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


def generate_round(module: ir.Module) -> None:
    """Generate round function: round(f64) -> f64

    Uses LLVM's round intrinsic (rounds to nearest integer, ties away from zero).
    """
    f64 = ir.DoubleType()

    # Declare LLVM round intrinsic
    intrinsic_name = "llvm.round.f64"
    intrinsic_type = ir.FunctionType(f64, [f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    # Create our wrapper function
    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_round")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call LLVM intrinsic
    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


def generate_trunc(module: ir.Module) -> None:
    """Generate trunc function: trunc(f64) -> f64

    Uses LLVM's trunc intrinsic (removes fractional part, rounds toward zero).
    """
    f64 = ir.DoubleType()

    # Declare LLVM trunc intrinsic
    intrinsic_name = "llvm.trunc.f64"
    intrinsic_type = ir.FunctionType(f64, [f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    # Create our wrapper function
    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_trunc")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call LLVM intrinsic
    result = builder.call(intrinsic, [x_param])
    builder.ret(result)
