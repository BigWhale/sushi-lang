"""Sleep function implementations for Sushi time module."""
from __future__ import annotations
import typing
from llvmlite import ir
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types, get_timespec_type

_platform_time = get_platform_module('time')

if typing.TYPE_CHECKING:
    pass


def generate_nanosleep(module: ir.Module) -> None:
    """Generate nanosleep function: nanosleep(i64 seconds, i64 nanoseconds) -> i32"""
    _, _, i32, i64 = get_basic_types()
    timespec_type = get_timespec_type()

    libc_nanosleep = _platform_time.declare_nanosleep(module)

    # Define OUR function signature: sushi_nanosleep(i64 seconds, i64 nanoseconds) -> i32
    # Note: Wrapping in Result happens at semantic level
    # We use sushi_ prefix to avoid name collision with the external C function
    func_type = ir.FunctionType(i32, [i64, i64])
    func = ir.Function(module, func_type, name="sushi_nanosleep")

    seconds_param = func.args[0]
    nanoseconds_param = func.args[1]
    seconds_param.name = "seconds"
    nanoseconds_param.name = "nanoseconds"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    req = builder.alloca(timespec_type, name="req")
    rem = builder.alloca(timespec_type, name="rem")

    req_sec_ptr = builder.gep(req, [i32(0), i32(0)], name="req.tv_sec.ptr")
    builder.store(seconds_param, req_sec_ptr)

    req_nsec_ptr = builder.gep(req, [i32(0), i32(1)], name="req.tv_nsec.ptr")
    builder.store(nanoseconds_param, req_nsec_ptr)

    result = builder.call(libc_nanosleep, [req, rem], name="nanosleep_result")

    zero = ir.Constant(i32, 0)
    minus_one = ir.Constant(i32, -1)
    was_interrupted = builder.icmp_signed('==', result, minus_one, name="was_interrupted")

    interrupted_block = func.append_basic_block("interrupted")
    completed_block = func.append_basic_block("completed")

    builder.cbranch(was_interrupted, interrupted_block, completed_block)

    builder.position_at_end(interrupted_block)

    rem_sec_ptr = builder.gep(rem, [i32(0), i32(0)])
    rem_nsec_ptr = builder.gep(rem, [i32(0), i32(1)])
    rem_sec = builder.load(rem_sec_ptr, name="rem.tv_sec")
    rem_nsec = builder.load(rem_nsec_ptr, name="rem.tv_nsec")

    million = ir.Constant(i64, 1_000_000)
    thousand = ir.Constant(i64, 1_000)

    rem_sec_micros = builder.mul(rem_sec, million, name="rem_sec_micros")
    rem_nsec_micros = builder.sdiv(rem_nsec, thousand, name="rem_nsec_micros")
    remaining_micros = builder.add(rem_sec_micros, rem_nsec_micros, name="remaining_micros")

    remaining_i32 = builder.trunc(remaining_micros, i32, name="remaining_i32")

    builder.ret(remaining_i32)

    builder.position_at_end(completed_block)
    builder.ret(zero)


def generate_sleep(module: ir.Module) -> None:
    """Generate sleep function: sleep(i64 seconds) -> Result<i32>"""
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    nanosleep_func = module.globals.get("sushi_nanosleep")
    if nanosleep_func is None:
        raise RuntimeError("sushi_nanosleep must be defined before sleep")

    func_type = ir.FunctionType(i32, [i64])
    func = ir.Function(module, func_type, name="sushi_sleep")

    seconds_param = func.args[0]
    seconds_param.name = "seconds"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    zero_nsec = ir.Constant(i64, 0)
    result = builder.call(nanosleep_func, [seconds_param, zero_nsec])

    builder.ret(result)


def generate_msleep(module: ir.Module) -> None:
    """Generate msleep function: msleep(i64 milliseconds) -> Result<i32>"""
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    nanosleep_func = module.globals.get("sushi_nanosleep")
    if nanosleep_func is None:
        raise RuntimeError("sushi_nanosleep must be defined before msleep")

    func_type = ir.FunctionType(i32, [i64])
    func = ir.Function(module, func_type, name="sushi_msleep")

    millis_param = func.args[0]
    millis_param.name = "milliseconds"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Convert milliseconds to seconds and nanoseconds
    # seconds = millis / 1000
    # nanoseconds = (millis % 1000) * 1_000_000

    thousand = ir.Constant(i64, 1000)
    million = ir.Constant(i64, 1_000_000)

    seconds = builder.sdiv(millis_param, thousand, name="seconds")
    millis_remainder = builder.srem(millis_param, thousand, name="millis_remainder")
    nanoseconds = builder.mul(millis_remainder, million, name="nanoseconds")

    result = builder.call(nanosleep_func, [seconds, nanoseconds])

    builder.ret(result)


def generate_usleep(module: ir.Module) -> None:
    """Generate usleep function: usleep(i64 microseconds) -> Result<i32>"""
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    nanosleep_func = module.globals.get("sushi_nanosleep")
    if nanosleep_func is None:
        raise RuntimeError("sushi_nanosleep must be defined before usleep")

    func_type = ir.FunctionType(i32, [i64])
    func = ir.Function(module, func_type, name="sushi_usleep")

    micros_param = func.args[0]
    micros_param.name = "microseconds"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Convert microseconds to seconds and nanoseconds
    # seconds = micros / 1_000_000
    # nanoseconds = (micros % 1_000_000) * 1_000

    million = ir.Constant(i64, 1_000_000)
    thousand = ir.Constant(i64, 1_000)

    seconds = builder.sdiv(micros_param, million, name="seconds")
    micros_remainder = builder.srem(micros_param, million, name="micros_remainder")
    nanoseconds = builder.mul(micros_remainder, thousand, name="nanoseconds")

    result = builder.call(nanosleep_func, [seconds, nanoseconds])

    builder.ret(result)
