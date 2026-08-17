"""Random number generator implementations for Sushi stdlib."""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types

_platform_random = get_platform_module('random')

if typing.TYPE_CHECKING:
    pass


def generate_rand(module: ir.Module) -> None:
    """Generate rand function: rand() -> u64"""
    _, _, i32, i64 = get_basic_types()

    libc_random = _platform_random.declare_random(module)

    # Define OUR function signature: sushi_rand() -> u64
    # Note: We use sushi_ prefix to avoid name collision with libc rand()
    func_type = ir.FunctionType(i64, [])
    func = ir.Function(module, func_type, name="sushi_rand")

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    low_i64 = builder.call(libc_random, [], name="random_low")

    high_i64 = builder.call(libc_random, [], name="random_high")

    shift_amount = ir.Constant(i64, 31)
    high_shifted = builder.shl(high_i64, shift_amount, name="high_shifted")

    result = builder.or_(high_shifted, low_i64, name="combined")

    builder.ret(result)


def generate_rand_range(module: ir.Module) -> None:
    """Generate rand_range function: rand_range(i32 min, i32 max) -> i32"""
    _, _, i32, i64 = get_basic_types()

    rand_func = module.globals.get("sushi_rand")
    if rand_func is None:
        raise RuntimeError("sushi_rand must be defined before rand_range")

    func_type = ir.FunctionType(i32, [i32, i32])
    func = ir.Function(module, func_type, name="sushi_rand_range")

    min_param = func.args[0]
    max_param = func.args[1]
    min_param.name = "min"
    max_param.name = "max"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    range_i32 = builder.sub(max_param, min_param, name="range")

    rand_u64 = builder.call(rand_func, [], name="rand_value")

    rand_i32 = builder.trunc(rand_u64, i32, name="rand_i32")

    remainder = builder.urem(rand_i32, range_i32, name="remainder")

    result = builder.add(min_param, remainder, name="result")

    builder.ret(result)


def generate_srand(module: ir.Module) -> None:
    """Generate srand function: srand(u64 seed) -> ~"""
    _, _, i32, i64 = get_basic_types()
    void = ir.VoidType()

    libc_srandom = _platform_random.declare_srandom(module)

    func_type = ir.FunctionType(void, [i64])
    func = ir.Function(module, func_type, name="sushi_srand")

    seed_param = func.args[0]
    seed_param.name = "seed"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    seed_i32 = builder.trunc(seed_param, i32, name="seed_i32")

    builder.call(libc_srandom, [seed_i32])

    builder.ret_void()


def generate_rand_f64(module: ir.Module) -> None:
    """Generate rand_f64 function: rand_f64() -> f64"""
    _, _, _, i64 = get_basic_types()
    f64 = ir.DoubleType()

    rand_func = module.globals.get("sushi_rand")
    if rand_func is None:
        raise RuntimeError("sushi_rand must be defined before rand_f64")

    func_type = ir.FunctionType(f64, [])
    func = ir.Function(module, func_type, name="sushi_rand_f64")

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    rand_u64 = builder.call(rand_func, [], name="rand_value")

    rand_f64 = builder.uitofp(rand_u64, f64, name="rand_f64")

    max_u64_plus_1 = ir.Constant(f64, 18446744073709551616.0)
    result = builder.fdiv(rand_f64, max_u64_plus_1, name="normalized")

    builder.ret(result)
