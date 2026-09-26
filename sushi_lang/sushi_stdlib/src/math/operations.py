"""Math operation implementations for Sushi math module."""
from __future__ import annotations
from llvmlite import ir


def _generate_f64_intrinsic_wrapper(module: ir.Module, llvm_name: str, sushi_name: str, arg_names: tuple) -> None:
    """Emit a `sushi_<name>` wrapper that forwards to an f64 LLVM intrinsic or libc function."""
    f64 = ir.DoubleType()
    sig = ir.FunctionType(f64, [f64] * len(arg_names))
    intrinsic = module.globals.get(llvm_name)
    if intrinsic is None:
        intrinsic = ir.Function(module, sig, name=llvm_name)

    func = ir.Function(module, ir.FunctionType(f64, [f64] * len(arg_names)), name=sushi_name)
    for arg, name in zip(func.args, arg_names, strict=True):
        arg.name = name

    builder = ir.IRBuilder(func.append_basic_block("entry"))
    builder.ret(builder.call(intrinsic, func.args))


def generate_abs_functions(module: ir.Module) -> None:
    """Generate abs() functions for all signed numeric types."""
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

        zero = ir.Constant(int_type, 0)
        is_negative = builder.icmp_signed('<', value_param, zero, name="is_negative")

        negative_block = func.append_basic_block("negative")
        positive_block = func.append_basic_block("positive")

        builder.cbranch(is_negative, negative_block, positive_block)

        builder.position_at_end(negative_block)
        negated = builder.sub(zero, value_param, name="negated")
        builder.ret(negated)

        builder.position_at_end(positive_block)
        builder.ret(value_param)

    float_types = [
        (ir.FloatType(), 'f32'),
        (ir.DoubleType(), 'f64'),
    ]

    for float_type, type_name in float_types:
        intrinsic_name = f"llvm.fabs.{type_name}"
        intrinsic_type = ir.FunctionType(float_type, [float_type])
        intrinsic = ir.Function(module, intrinsic_type, name=intrinsic_name)

        func_type = ir.FunctionType(float_type, [float_type])
        func = ir.Function(module, func_type, name=f"sushi_abs_{type_name}")

        value_param = func.args[0]
        value_param.name = "value"

        entry = func.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        result = builder.call(intrinsic, [value_param])
        builder.ret(result)


def generate_min_max_functions(module: ir.Module) -> None:
    """Generate min() and max() functions for all numeric types."""
    signed_int_types = [
        (ir.IntType(8), 'i8', True),
        (ir.IntType(16), 'i16', True),
        (ir.IntType(32), 'i32', True),
        (ir.IntType(64), 'i64', True),
    ]

    unsigned_int_types = [
        (ir.IntType(8), 'u8', False),
        (ir.IntType(16), 'u16', False),
        (ir.IntType(32), 'u32', False),
        (ir.IntType(64), 'u64', False),
    ]

    float_types = [
        (ir.FloatType(), 'f32'),
        (ir.DoubleType(), 'f64'),
    ]

    for int_type, type_name, is_signed in signed_int_types + unsigned_int_types:
        func_type = ir.FunctionType(int_type, [int_type, int_type])
        min_func = ir.Function(module, func_type, name=f"sushi_min_{type_name}")

        a_param = min_func.args[0]
        b_param = min_func.args[1]
        a_param.name = "a"
        b_param.name = "b"

        entry = min_func.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        if is_signed:
            cmp = builder.icmp_signed('<', a_param, b_param, name="cmp")
        else:
            cmp = builder.icmp_unsigned('<', a_param, b_param, name="cmp")

        result = builder.select(cmp, a_param, b_param, name="min")
        builder.ret(result)

        max_func = ir.Function(module, func_type, name=f"sushi_max_{type_name}")

        a_param = max_func.args[0]
        b_param = max_func.args[1]
        a_param.name = "a"
        b_param.name = "b"

        entry = max_func.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        if is_signed:
            cmp = builder.icmp_signed('>', a_param, b_param, name="cmp")
        else:
            cmp = builder.icmp_unsigned('>', a_param, b_param, name="cmp")

        result = builder.select(cmp, a_param, b_param, name="max")
        builder.ret(result)

    for float_type, type_name in float_types:
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
    """Generate sushi_sqrt(f64 x) -> f64 via the llvm.sqrt.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.sqrt.f64", "sushi_sqrt", ('x',))


def generate_pow(module: ir.Module) -> None:
    """Generate sushi_pow(f64 base, f64 exponent) -> f64 via the llvm.pow.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.pow.f64", "sushi_pow", ('base', 'exponent'))


def generate_floor(module: ir.Module) -> None:
    """Generate sushi_floor(f64 x) -> f64 via the llvm.floor.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.floor.f64", "sushi_floor", ('x',))


def generate_ceil(module: ir.Module) -> None:
    """Generate sushi_ceil(f64 x) -> f64 via the llvm.ceil.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.ceil.f64", "sushi_ceil", ('x',))


def generate_round(module: ir.Module) -> None:
    """Generate sushi_round(f64 x) -> f64 via the llvm.round.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.round.f64", "sushi_round", ('x',))


def generate_trunc(module: ir.Module) -> None:
    """Generate sushi_trunc(f64 x) -> f64 via the llvm.trunc.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.trunc.f64", "sushi_trunc", ('x',))


def generate_sin(module: ir.Module) -> None:
    """Generate sushi_sin(f64 x) -> f64 via the llvm.sin.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.sin.f64", "sushi_sin", ('x',))


def generate_cos(module: ir.Module) -> None:
    """Generate sushi_cos(f64 x) -> f64 via the llvm.cos.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.cos.f64", "sushi_cos", ('x',))


def generate_tan(module: ir.Module) -> None:
    """Generate tan function: tan(f64) -> f64"""
    f64 = ir.DoubleType()

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


def generate_asin(module: ir.Module) -> None:
    """Generate asin function: asin(f64) -> f64"""
    f64 = ir.DoubleType()

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
    """Generate acos function: acos(f64) -> f64"""
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
    """Generate atan function: atan(f64) -> f64"""
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
    """Generate atan2 function: atan2(f64 y, f64 x) -> f64"""
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


def generate_sinh(module: ir.Module) -> None:
    """Generate sushi_sinh(f64 x) -> f64 via the libc sinh."""
    _generate_f64_intrinsic_wrapper(module, "sinh", "sushi_sinh", ('x',))


def generate_cosh(module: ir.Module) -> None:
    """Generate sushi_cosh(f64 x) -> f64 via the libc cosh."""
    _generate_f64_intrinsic_wrapper(module, "cosh", "sushi_cosh", ('x',))


def generate_tanh(module: ir.Module) -> None:
    """Generate sushi_tanh(f64 x) -> f64 via the libc tanh."""
    _generate_f64_intrinsic_wrapper(module, "tanh", "sushi_tanh", ('x',))


def generate_log(module: ir.Module) -> None:
    """Generate sushi_log(f64 x) -> f64 via the llvm.log.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.log.f64", "sushi_log", ('x',))


def generate_log2(module: ir.Module) -> None:
    """Generate sushi_log2(f64 x) -> f64 via the llvm.log2.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.log2.f64", "sushi_log2", ('x',))


def generate_log10(module: ir.Module) -> None:
    """Generate sushi_log10(f64 x) -> f64 via the llvm.log10.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.log10.f64", "sushi_log10", ('x',))


def generate_exp(module: ir.Module) -> None:
    """Generate sushi_exp(f64 x) -> f64 via the llvm.exp.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.exp.f64", "sushi_exp", ('x',))


def generate_exp2(module: ir.Module) -> None:
    """Generate sushi_exp2(f64 x) -> f64 via the llvm.exp2.f64 intrinsic."""
    _generate_f64_intrinsic_wrapper(module, "llvm.exp2.f64", "sushi_exp2", ('x',))


def generate_hypot(module: ir.Module) -> None:
    """Generate sushi_hypot(f64 x, f64 y) -> f64 via the libc hypot."""
    _generate_f64_intrinsic_wrapper(module, "hypot", "sushi_hypot", ('x', 'y'))
