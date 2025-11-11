"""
Sleep function implementations for Sushi time module.

Implements sleep functions with varying time granularities:
- nanosleep: Core implementation with nanosecond precision
- sleep: Convenience wrapper (seconds)
- msleep: Convenience wrapper (milliseconds)
- usleep: Convenience wrapper (microseconds)

All functions use POSIX nanosleep() under the hood for consistency
and portability across Unix-like systems.
"""
from __future__ import annotations
import typing
from llvmlite import ir
from stdlib.src._platform import get_platform_module
from stdlib.src.type_definitions import get_basic_types, get_timespec_type

# Get platform-specific time module (darwin, linux, windows, etc.)
_platform_time = get_platform_module('time')

if typing.TYPE_CHECKING:
    pass


def generate_nanosleep(module: ir.Module) -> None:
    """Generate nanosleep function: nanosleep(i64 seconds, i64 nanoseconds) -> i32

    Sleeps for the specified duration. If interrupted by a signal, returns the
    remaining time in microseconds. Returns 0 on successful completion.

    Implementation:
        1. Declare external nanosleep from platform layer
        2. Allocate timespec struct on stack
        3. Fill in tv_sec and tv_nsec fields
        4. Call nanosleep(&req, &rem)
        5. Check return value:
           - If 0: Return 0 (success)
           - If -1: Calculate remaining microseconds, return remaining time
        6. Return bare i32 (wrapping in Result<T> happens at semantic level)
    """
    # Get common types
    _, _, i32, i64 = get_basic_types()
    timespec_type = get_timespec_type()

    # Declare external C nanosleep (takes timespec pointers)
    # Uses platform-specific declaration (darwin, linux, windows, etc.)
    libc_nanosleep = _platform_time.declare_nanosleep(module)

    # Define OUR function signature: sushi_nanosleep(i64 seconds, i64 nanoseconds) -> i32
    # Note: Wrapping in Result happens at semantic level
    # We use sushi_ prefix to avoid name collision with the external C function
    func_type = ir.FunctionType(i32, [i64, i64])
    func = ir.Function(module, func_type, name="sushi_nanosleep")

    # Get parameters
    seconds_param = func.args[0]
    nanoseconds_param = func.args[1]
    seconds_param.name = "seconds"
    nanoseconds_param.name = "nanoseconds"

    # Create entry block
    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Allocate timespec structs on stack
    req = builder.alloca(timespec_type, name="req")
    rem = builder.alloca(timespec_type, name="rem")

    # Fill in req.tv_sec
    req_sec_ptr = builder.gep(req, [i32(0), i32(0)], name="req.tv_sec.ptr")
    builder.store(seconds_param, req_sec_ptr)

    # Fill in req.tv_nsec
    req_nsec_ptr = builder.gep(req, [i32(0), i32(1)], name="req.tv_nsec.ptr")
    builder.store(nanoseconds_param, req_nsec_ptr)

    # Call libc nanosleep(&req, &rem)
    result = builder.call(libc_nanosleep, [req, rem], name="nanosleep_result")

    # Check if sleep was interrupted (result == -1)
    zero = ir.Constant(i32, 0)
    minus_one = ir.Constant(i32, -1)
    was_interrupted = builder.icmp_signed('==', result, minus_one, name="was_interrupted")

    # Create blocks for interrupted and completed cases
    interrupted_block = func.append_basic_block("interrupted")
    completed_block = func.append_basic_block("completed")

    builder.cbranch(was_interrupted, interrupted_block, completed_block)

    # Interrupted block: Calculate remaining microseconds
    builder.position_at_end(interrupted_block)

    # Load rem.tv_sec and rem.tv_nsec
    rem_sec_ptr = builder.gep(rem, [i32(0), i32(0)])
    rem_nsec_ptr = builder.gep(rem, [i32(0), i32(1)])
    rem_sec = builder.load(rem_sec_ptr, name="rem.tv_sec")
    rem_nsec = builder.load(rem_nsec_ptr, name="rem.tv_nsec")

    # Convert to microseconds: (sec * 1_000_000) + (nsec / 1_000)
    million = ir.Constant(i64, 1_000_000)
    thousand = ir.Constant(i64, 1_000)

    rem_sec_micros = builder.mul(rem_sec, million, name="rem_sec_micros")
    rem_nsec_micros = builder.sdiv(rem_nsec, thousand, name="rem_nsec_micros")
    remaining_micros = builder.add(rem_sec_micros, rem_nsec_micros, name="remaining_micros")

    # Truncate to i32 (microseconds should fit in i32 for reasonable sleep durations)
    remaining_i32 = builder.trunc(remaining_micros, i32, name="remaining_i32")

    builder.ret(remaining_i32)

    # Completed block: Return 0
    builder.position_at_end(completed_block)
    builder.ret(zero)


def generate_sleep(module: ir.Module) -> None:
    """
    Generate sleep function: sleep(i64 seconds) -> Result<i32>

    Convenience wrapper that converts seconds to nanosleep call.
    """
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    # Get sushi_nanosleep function (should already be defined)
    nanosleep_func = module.globals.get("sushi_nanosleep")
    if nanosleep_func is None:
        raise RuntimeError("sushi_nanosleep must be defined before sleep")

    # Define function signature: sushi_sleep(i64 seconds) -> i32
    func_type = ir.FunctionType(i32, [i64])
    func = ir.Function(module, func_type, name="sushi_sleep")

    seconds_param = func.args[0]
    seconds_param.name = "seconds"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call sushi_nanosleep(seconds, 0)
    zero_nsec = ir.Constant(i64, 0)
    result = builder.call(nanosleep_func, [seconds_param, zero_nsec])

    builder.ret(result)


def generate_msleep(module: ir.Module) -> None:
    """
    Generate msleep function: msleep(i64 milliseconds) -> Result<i32>

    Convenience wrapper that converts milliseconds to nanosleep call.
    Conversion: 1 ms = 1_000_000 ns
    """
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    # Get sushi_nanosleep function
    nanosleep_func = module.globals.get("sushi_nanosleep")
    if nanosleep_func is None:
        raise RuntimeError("sushi_nanosleep must be defined before msleep")

    # Define function signature: sushi_msleep(i64 milliseconds) -> i32
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

    # Call sushi_nanosleep(seconds, nanoseconds)
    result = builder.call(nanosleep_func, [seconds, nanoseconds])

    builder.ret(result)


def generate_usleep(module: ir.Module) -> None:
    """
    Generate usleep function: usleep(i64 microseconds) -> Result<i32>

    Convenience wrapper that converts microseconds to nanosleep call.
    Conversion: 1 μs = 1_000 ns
    """
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    # Get sushi_nanosleep function
    nanosleep_func = module.globals.get("sushi_nanosleep")
    if nanosleep_func is None:
        raise RuntimeError("sushi_nanosleep must be defined before usleep")

    # Define function signature: sushi_usleep(i64 microseconds) -> i32
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

    # Call sushi_nanosleep(seconds, nanoseconds)
    result = builder.call(nanosleep_func, [seconds, nanoseconds])

    builder.ret(result)
