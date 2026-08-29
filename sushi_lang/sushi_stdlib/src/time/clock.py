"""Clock functions for the <time> module: now() and monotonic_ns()."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types, get_timespec_type
from sushi_lang.sushi_stdlib.src._platform import get_platform_module


def _generate_clock_read(module: ir.Module, sushi_name: str, clock_id: int,
                         to_nanoseconds: bool) -> None:
    """Emit `i64 <sushi_name>()`: one clock_gettime read.

    Returns seconds (unix clock) or whole nanoseconds (monotonic clock).
    A clock_gettime failure returns -1; both registered clocks cannot fail
    on macOS or Linux for a valid clockid.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    platform_time = get_platform_module('time')
    clock_gettime = platform_time.declare_clock_gettime(module)
    timespec = get_timespec_type()

    func = ir.Function(module, ir.FunctionType(i64, []), name=sushi_name)
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    ts = builder.alloca(timespec, name="ts")
    result = builder.call(clock_gettime, [ir.Constant(i32, clock_id), ts], name="cg_result")

    ok = builder.icmp_signed("==", result, ir.Constant(i32, 0), name="cg_ok")
    ok_bb = func.append_basic_block(name="ok")
    fail_bb = func.append_basic_block(name="fail")
    builder.cbranch(ok, ok_bb, fail_bb)

    builder.position_at_end(fail_bb)
    builder.ret(ir.Constant(i64, -1))

    builder.position_at_end(ok_bb)
    zero = ir.Constant(i32, 0)
    sec_ptr = builder.gep(ts, [zero, ir.Constant(i32, 0)], name="sec_ptr")
    sec = builder.load(sec_ptr, name="sec")
    if to_nanoseconds:
        nsec_ptr = builder.gep(ts, [zero, ir.Constant(i32, 1)], name="nsec_ptr")
        nsec = builder.load(nsec_ptr, name="nsec")
        total = builder.add(builder.mul(sec, ir.Constant(i64, 1_000_000_000)), nsec, name="total_ns")
        builder.ret(total)
    else:
        builder.ret(sec)


def generate_now(module: ir.Module) -> None:
    """Generate sushi_now() -> i64 (unix seconds)."""
    platform_time = get_platform_module('time')
    _generate_clock_read(module, "sushi_now", platform_time.CLOCK_REALTIME,
                         to_nanoseconds=False)


def generate_monotonic_ns(module: ir.Module) -> None:
    """Generate sushi_monotonic_ns() -> i64 (nanoseconds on the monotonic clock)."""
    platform_time = get_platform_module('time')
    _generate_clock_read(module, "sushi_monotonic_ns", platform_time.CLOCK_MONOTONIC,
                         to_nanoseconds=True)
