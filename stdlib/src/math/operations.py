"""
Math operation implementations for Sushi math module.

Implements mathematical functions:
- abs: Absolute value for signed numeric types
- min/max: Minimum and maximum for all numeric types
- sqrt: Square root using LLVM intrinsic
- pow: Power function using LLVM intrinsic
- floor/ceil/round/trunc: Rounding functions using LLVM intrinsics
- sin/cos: Trigonometric using LLVM intrinsics
- tan: Trigonometric using pure LLVM IR (sin/cos)
- asin/acos/atan/atan2: Inverse trig using libc
- sinh/cosh/tanh: Hyperbolic using pure LLVM IR (exp)
- log/log2/log10: Logarithmic using LLVM intrinsics
- exp/exp2: Exponential using LLVM intrinsics
- hypot: Utility using pure LLVM IR (sqrt)

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


# =============================================================================
# Trigonometric Functions
# =============================================================================

def generate_sin(module: ir.Module) -> None:
    """Generate sin function: sin(f64) -> f64

    Uses LLVM's sin intrinsic.
    """
    f64 = ir.DoubleType()

    intrinsic_name = "llvm.sin.f64"
    intrinsic_type = ir.FunctionType(f64, [f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_sin")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


def generate_cos(module: ir.Module) -> None:
    """Generate cos function: cos(f64) -> f64

    Uses LLVM's cos intrinsic.
    """
    f64 = ir.DoubleType()

    intrinsic_name = "llvm.cos.f64"
    intrinsic_type = ir.FunctionType(f64, [f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_cos")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


def generate_tan(module: ir.Module) -> None:
    """Generate tan function: tan(f64) -> f64

    Implemented as sin(x) / cos(x) in pure LLVM IR.
    """
    f64 = ir.DoubleType()

    # Get or declare sin and cos intrinsics
    sin_intrinsic = module.globals.get("llvm.sin.f64")
    if sin_intrinsic is None:
        sin_type = ir.FunctionType(f64, [f64])
        sin_intrinsic = ir.Function(module, sin_type, name="llvm.sin.f64")

    cos_intrinsic = module.globals.get("llvm.cos.f64")
    if cos_intrinsic is None:
        cos_type = ir.FunctionType(f64, [f64])
        cos_intrinsic = ir.Function(module, cos_type, name="llvm.cos.f64")

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_tan")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    sin_x = builder.call(sin_intrinsic, [x_param], name="sin_x")
    cos_x = builder.call(cos_intrinsic, [x_param], name="cos_x")
    result = builder.fdiv(sin_x, cos_x, name="tan_x")
    builder.ret(result)


# =============================================================================
# Inverse Trigonometric Functions (libc)
# =============================================================================

def generate_asin(module: ir.Module) -> None:
    """Generate asin function: asin(f64) -> f64

    Uses libc asin().
    """
    f64 = ir.DoubleType()

    # Declare libc asin
    libc_asin_type = ir.FunctionType(f64, [f64])
    libc_asin = ir.Function(module, libc_asin_type, name="asin")

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_asin")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(libc_asin, [x_param])
    builder.ret(result)


def generate_acos(module: ir.Module) -> None:
    """Generate acos function: acos(f64) -> f64

    Uses libc acos().
    """
    f64 = ir.DoubleType()

    libc_acos_type = ir.FunctionType(f64, [f64])
    libc_acos = ir.Function(module, libc_acos_type, name="acos")

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_acos")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(libc_acos, [x_param])
    builder.ret(result)


def generate_atan(module: ir.Module) -> None:
    """Generate atan function: atan(f64) -> f64

    Uses libc atan().
    """
    f64 = ir.DoubleType()

    libc_atan_type = ir.FunctionType(f64, [f64])
    libc_atan = ir.Function(module, libc_atan_type, name="atan")

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_atan")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(libc_atan, [x_param])
    builder.ret(result)


def generate_atan2(module: ir.Module) -> None:
    """Generate atan2 function: atan2(f64 y, f64 x) -> f64

    Uses libc atan2().
    """
    f64 = ir.DoubleType()

    libc_atan2_type = ir.FunctionType(f64, [f64, f64])
    libc_atan2 = ir.Function(module, libc_atan2_type, name="atan2")

    func_type = ir.FunctionType(f64, [f64, f64])
    func = ir.Function(module, func_type, name="sushi_atan2")

    y_param = func.args[0]
    x_param = func.args[1]
    y_param.name = "y"
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(libc_atan2, [y_param, x_param])
    builder.ret(result)


# =============================================================================
# Hyperbolic Functions (pure LLVM IR using exp)
# =============================================================================

def generate_sinh(module: ir.Module) -> None:
    """Generate sinh function: sinh(f64) -> f64

    Implemented as (exp(x) - exp(-x)) / 2 in pure LLVM IR.
    """
    f64 = ir.DoubleType()

    # Get or declare exp intrinsic
    exp_intrinsic = module.globals.get("llvm.exp.f64")
    if exp_intrinsic is None:
        exp_type = ir.FunctionType(f64, [f64])
        exp_intrinsic = ir.Function(module, exp_type, name="llvm.exp.f64")

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_sinh")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # exp(x)
    exp_x = builder.call(exp_intrinsic, [x_param], name="exp_x")

    # -x
    neg_x = builder.fneg(x_param, name="neg_x")

    # exp(-x)
    exp_neg_x = builder.call(exp_intrinsic, [neg_x], name="exp_neg_x")

    # exp(x) - exp(-x)
    diff = builder.fsub(exp_x, exp_neg_x, name="diff")

    # (exp(x) - exp(-x)) / 2
    two = ir.Constant(f64, 2.0)
    result = builder.fdiv(diff, two, name="sinh_x")
    builder.ret(result)


def generate_cosh(module: ir.Module) -> None:
    """Generate cosh function: cosh(f64) -> f64

    Implemented as (exp(x) + exp(-x)) / 2 in pure LLVM IR.
    """
    f64 = ir.DoubleType()

    exp_intrinsic = module.globals.get("llvm.exp.f64")
    if exp_intrinsic is None:
        exp_type = ir.FunctionType(f64, [f64])
        exp_intrinsic = ir.Function(module, exp_type, name="llvm.exp.f64")

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_cosh")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    exp_x = builder.call(exp_intrinsic, [x_param], name="exp_x")
    neg_x = builder.fneg(x_param, name="neg_x")
    exp_neg_x = builder.call(exp_intrinsic, [neg_x], name="exp_neg_x")

    # exp(x) + exp(-x)
    sum_val = builder.fadd(exp_x, exp_neg_x, name="sum")

    two = ir.Constant(f64, 2.0)
    result = builder.fdiv(sum_val, two, name="cosh_x")
    builder.ret(result)


def generate_tanh(module: ir.Module) -> None:
    """Generate tanh function: tanh(f64) -> f64

    Implemented as (exp(x) - exp(-x)) / (exp(x) + exp(-x)) in pure LLVM IR.
    """
    f64 = ir.DoubleType()

    exp_intrinsic = module.globals.get("llvm.exp.f64")
    if exp_intrinsic is None:
        exp_type = ir.FunctionType(f64, [f64])
        exp_intrinsic = ir.Function(module, exp_type, name="llvm.exp.f64")

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_tanh")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    exp_x = builder.call(exp_intrinsic, [x_param], name="exp_x")
    neg_x = builder.fneg(x_param, name="neg_x")
    exp_neg_x = builder.call(exp_intrinsic, [neg_x], name="exp_neg_x")

    # numerator: exp(x) - exp(-x)
    numer = builder.fsub(exp_x, exp_neg_x, name="numer")

    # denominator: exp(x) + exp(-x)
    denom = builder.fadd(exp_x, exp_neg_x, name="denom")

    result = builder.fdiv(numer, denom, name="tanh_x")
    builder.ret(result)


# =============================================================================
# Logarithmic Functions
# =============================================================================

def generate_log(module: ir.Module) -> None:
    """Generate log function: log(f64) -> f64 (natural logarithm)

    Uses LLVM's log intrinsic.
    """
    f64 = ir.DoubleType()

    intrinsic_name = "llvm.log.f64"
    intrinsic_type = ir.FunctionType(f64, [f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_log")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


def generate_log2(module: ir.Module) -> None:
    """Generate log2 function: log2(f64) -> f64

    Uses LLVM's log2 intrinsic.
    """
    f64 = ir.DoubleType()

    intrinsic_name = "llvm.log2.f64"
    intrinsic_type = ir.FunctionType(f64, [f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_log2")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


def generate_log10(module: ir.Module) -> None:
    """Generate log10 function: log10(f64) -> f64

    Uses LLVM's log10 intrinsic.
    """
    f64 = ir.DoubleType()

    intrinsic_name = "llvm.log10.f64"
    intrinsic_type = ir.FunctionType(f64, [f64])
    intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_log10")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


# =============================================================================
# Exponential Functions
# =============================================================================

def generate_exp(module: ir.Module) -> None:
    """Generate exp function: exp(f64) -> f64

    Uses LLVM's exp intrinsic.
    """
    f64 = ir.DoubleType()

    # Get or declare exp intrinsic (may already exist from hyperbolic functions)
    intrinsic = module.globals.get("llvm.exp.f64")
    if intrinsic is None:
        intrinsic_type = ir.FunctionType(f64, [f64])
        intrinsic = ir.Function(module, intrinsic_type, name="llvm.exp.f64")

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_exp")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


def generate_exp2(module: ir.Module) -> None:
    """Generate exp2 function: exp2(f64) -> f64

    Uses LLVM's exp2 intrinsic.
    """
    f64 = ir.DoubleType()

    # Get or declare exp2 intrinsic
    intrinsic = module.globals.get("llvm.exp2.f64")
    if intrinsic is None:
        intrinsic_type = ir.FunctionType(f64, [f64])
        intrinsic = ir.Function(module, intrinsic_type, name="llvm.exp2.f64")

    func_type = ir.FunctionType(f64, [f64])
    func = ir.Function(module, func_type, name="sushi_exp2")

    x_param = func.args[0]
    x_param.name = "x"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(intrinsic, [x_param])
    builder.ret(result)


# =============================================================================
# Utility Functions
# =============================================================================

def generate_hypot(module: ir.Module) -> None:
    """Generate hypot function: hypot(f64 x, f64 y) -> f64

    Implemented as sqrt(x*x + y*y) in pure LLVM IR.
    """
    f64 = ir.DoubleType()

    # Get or declare sqrt intrinsic
    sqrt_intrinsic = module.globals.get("llvm.sqrt.f64")
    if sqrt_intrinsic is None:
        sqrt_type = ir.FunctionType(f64, [f64])
        sqrt_intrinsic = ir.Function(module, sqrt_type, name="llvm.sqrt.f64")

    func_type = ir.FunctionType(f64, [f64, f64])
    func = ir.Function(module, func_type, name="sushi_hypot")

    x_param = func.args[0]
    y_param = func.args[1]
    x_param.name = "x"
    y_param.name = "y"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # x * x
    x_sq = builder.fmul(x_param, x_param, name="x_sq")

    # y * y
    y_sq = builder.fmul(y_param, y_param, name="y_sq")

    # x*x + y*y
    sum_sq = builder.fadd(x_sq, y_sq, name="sum_sq")

    # sqrt(x*x + y*y)
    result = builder.call(sqrt_intrinsic, [sum_sq], name="hypot")
    builder.ret(result)
