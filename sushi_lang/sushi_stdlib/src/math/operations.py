"""Math operation implementations for Sushi math module."""
from __future__ import annotations
from llvmlite import ir

from sushi_lang.sushi_stdlib.src.libc_declarations import declare_extern


def _forward_f64(module: ir.Module, llvm_name: str, sushi_name: str, arg_names: tuple) -> None:
    """Emit a `sushi_<name>` wrapper that forwards to an f64 LLVM intrinsic or libc function."""
    f64 = ir.DoubleType()
    sig = ir.FunctionType(f64, [f64] * len(arg_names))
    callee = declare_extern(module, llvm_name, f64, [f64] * len(arg_names))

    func = ir.Function(module, sig, name=sushi_name)
    for arg, name in zip(func.args, arg_names, strict=True):
        arg.name = name

    builder = ir.IRBuilder(func.append_basic_block("entry"))
    builder.ret(builder.call(callee, func.args))


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
        intrinsic = declare_extern(module, f"llvm.fabs.{type_name}", float_type, [float_type])

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
        min_intrinsic = declare_extern(
            module, f"llvm.minnum.{type_name}", float_type, [float_type, float_type])

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

        max_intrinsic = declare_extern(
            module, f"llvm.maxnum.{type_name}", float_type, [float_type, float_type])

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
    _forward_f64(module, "llvm.sqrt.f64", "sushi_sqrt", ('x',))


def generate_pow(module: ir.Module) -> None:
    """Generate sushi_pow(f64 base, f64 exponent) -> f64 via the llvm.pow.f64 intrinsic."""
    _forward_f64(module, "llvm.pow.f64", "sushi_pow", ('base', 'exponent'))


def generate_floor(module: ir.Module) -> None:
    """Generate sushi_floor(f64 x) -> f64 via the llvm.floor.f64 intrinsic."""
    _forward_f64(module, "llvm.floor.f64", "sushi_floor", ('x',))


def generate_ceil(module: ir.Module) -> None:
    """Generate sushi_ceil(f64 x) -> f64 via the llvm.ceil.f64 intrinsic."""
    _forward_f64(module, "llvm.ceil.f64", "sushi_ceil", ('x',))


def generate_round(module: ir.Module) -> None:
    """Generate sushi_round(f64 x) -> f64 via the llvm.round.f64 intrinsic."""
    _forward_f64(module, "llvm.round.f64", "sushi_round", ('x',))


def generate_trunc(module: ir.Module) -> None:
    """Generate sushi_trunc(f64 x) -> f64 via the llvm.trunc.f64 intrinsic."""
    _forward_f64(module, "llvm.trunc.f64", "sushi_trunc", ('x',))


def generate_sin(module: ir.Module) -> None:
    """Generate sushi_sin(f64 x) -> f64 via the llvm.sin.f64 intrinsic."""
    _forward_f64(module, "llvm.sin.f64", "sushi_sin", ('x',))


def generate_cos(module: ir.Module) -> None:
    """Generate sushi_cos(f64 x) -> f64 via the llvm.cos.f64 intrinsic."""
    _forward_f64(module, "llvm.cos.f64", "sushi_cos", ('x',))


def generate_tan(module: ir.Module) -> None:
    """Generate sushi_tan(f64 x) -> f64 via the libc tan."""
    _forward_f64(module, "tan", "sushi_tan", ('x',))


def generate_asin(module: ir.Module) -> None:
    """Generate sushi_asin(f64 x) -> f64 via the libc asin."""
    _forward_f64(module, "asin", "sushi_asin", ('x',))


def generate_acos(module: ir.Module) -> None:
    """Generate sushi_acos(f64 x) -> f64 via the libc acos."""
    _forward_f64(module, "acos", "sushi_acos", ('x',))


def generate_atan(module: ir.Module) -> None:
    """Generate sushi_atan(f64 x) -> f64 via the libc atan."""
    _forward_f64(module, "atan", "sushi_atan", ('x',))


def generate_atan2(module: ir.Module) -> None:
    """Generate sushi_atan2(f64 y, f64 x) -> f64 via the libc atan2."""
    _forward_f64(module, "atan2", "sushi_atan2", ('y', 'x'))


def generate_sinh(module: ir.Module) -> None:
    """Generate sushi_sinh(f64 x) -> f64 via the libc sinh."""
    _forward_f64(module, "sinh", "sushi_sinh", ('x',))


def generate_cosh(module: ir.Module) -> None:
    """Generate sushi_cosh(f64 x) -> f64 via the libc cosh."""
    _forward_f64(module, "cosh", "sushi_cosh", ('x',))


def generate_tanh(module: ir.Module) -> None:
    """Generate sushi_tanh(f64 x) -> f64 via the libc tanh."""
    _forward_f64(module, "tanh", "sushi_tanh", ('x',))


def generate_log(module: ir.Module) -> None:
    """Generate sushi_log(f64 x) -> f64 via the llvm.log.f64 intrinsic."""
    _forward_f64(module, "llvm.log.f64", "sushi_log", ('x',))


def generate_log2(module: ir.Module) -> None:
    """Generate sushi_log2(f64 x) -> f64 via the llvm.log2.f64 intrinsic."""
    _forward_f64(module, "llvm.log2.f64", "sushi_log2", ('x',))


def generate_log10(module: ir.Module) -> None:
    """Generate sushi_log10(f64 x) -> f64 via the llvm.log10.f64 intrinsic."""
    _forward_f64(module, "llvm.log10.f64", "sushi_log10", ('x',))


def generate_exp(module: ir.Module) -> None:
    """Generate sushi_exp(f64 x) -> f64 via the llvm.exp.f64 intrinsic."""
    _forward_f64(module, "llvm.exp.f64", "sushi_exp", ('x',))


def generate_exp2(module: ir.Module) -> None:
    """Generate sushi_exp2(f64 x) -> f64 via the llvm.exp2.f64 intrinsic."""
    _forward_f64(module, "llvm.exp2.f64", "sushi_exp2", ('x',))


def generate_hypot(module: ir.Module) -> None:
    """Generate sushi_hypot(f64 x, f64 y) -> f64 via the libc hypot."""
    _forward_f64(module, "hypot", "sushi_hypot", ('x', 'y'))
