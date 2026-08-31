"""The descriptor layer of <io/files>: open, positional read/write, dup, close.

These take a DESCRIPTOR rather than a path, which is what makes them the layer a
`File` struct is written on top of -- exactly as `<net/socket>` is the layer
`net/tcp.sushi` writes `TcpStream` on top of (HANDLES.md, Phase 4).

`fd_pread` and `fd_pwrite` take the offset as an ARGUMENT, so the descriptor's own
file position never moves. That is what makes them the answer for concurrent reads of
one file: nothing is shared, so nothing can race. Every language that supports
concurrent file I/O converged on the same primitive -- C and POSIX have pread(2) and
pwrite(2), Go has File.ReadAt, Rust has FileExt::read_at, Java takes a position
argument on FileChannel.read -- and none of them needed a new kind of type.

`off_t` is 64-bit on both supported platforms; probe P6 measured it.
"""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types, get_byte_array_type, get_result_type, get_unit_enum_type,
)
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.io.files.errno import (
    emit_errno_err_result, emit_file_error_tag)
from sushi_lang.sushi_stdlib.src.io.files.results import emit_ok_result, emit_err_result
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_free, declare_malloc
from sushi_lang.sushi_stdlib.src.error_emission import emit_runtime_error


def generate_ir(module: ir.Module) -> None:
    """Generate the descriptor-level primitives."""
    generate_fd_open(module)
    generate_fd_pread(module)
    generate_fd_pwrite(module)
    generate_fd_dup(module)
    generate_fd_close(module)


# What a caller WANTS, not what the platform calls it. A portable Sushi module cannot
# name `O_TRUNC`: the value differs between macOS and Linux and there is no conditional
# compilation, so a number spelled in a Sushi-source module would be wrong on one of
# them. The intent crosses the boundary and the platform module maps it, which is
# where the platform knowledge already lives. `FileMode` maps onto these in Phase 5.
INTENT_READ = 0
INTENT_WRITE = 1     # create, truncate
INTENT_APPEND = 2    # create, append
INTENT_READ_WRITE = 3  # create, keep


def generate_fd_open(module: ir.Module) -> None:
    """Emit `Result<i32, FileError> sushi_io_files_fd_open(i8* path, i32 intent, i32 mode)`.

    The path arrives already marshalled as a C string and the CALLER frees it, which is
    the one C-string seam every stdlib callee obeys. `intent` is one of the INTENT_*
    values above; anything else opens read-only, which is the safe reading of a number
    this function does not recognise.
    """
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    platform_files = get_platform_module('files')
    open_fn = platform_files.declare_open(module)

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i8_ptr, i32, i32]),
                       name="sushi_io_files_fd_open")
    path, intent, mode = func.args
    path.name, intent.name, mode.name = "path", "intent", "mode"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    flags_slot = builder.alloca(i32, name="flags_slot")
    builder.store(ir.Constant(i32, platform_files.O_RDONLY), flags_slot)

    # One block per intent, and the default is read-only rather than a trap: an
    # unrecognised number must not open a file for writing.
    done_bb = func.append_basic_block(name="flags_done")
    switch = builder.switch(intent, done_bb)
    for value, flags in (
        (INTENT_WRITE, platform_files.O_WRONLY | platform_files.O_CREAT
                       | platform_files.O_TRUNC),
        (INTENT_APPEND, platform_files.O_WRONLY | platform_files.O_CREAT
                        | platform_files.O_APPEND),
        (INTENT_READ_WRITE, platform_files.O_RDWR | platform_files.O_CREAT),
    ):
        block = func.append_basic_block(name=f"intent_{value}")
        switch.add_case(ir.Constant(i32, value), block)
        builder.position_at_end(block)
        builder.store(ir.Constant(i32, flags), flags_slot)
        builder.branch(done_bb)

    builder.position_at_end(done_bb)
    flags = builder.load(flags_slot, name="flags")

    fd = builder.call(open_fn, [path, flags, mode], name="fd")
    ok = builder.icmp_signed(">=", fd, ir.Constant(i32, 0), name="open_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, fd, 4))


def generate_fd_pread(module: ir.Module) -> None:
    """Emit `Result<u8[], FileError> sushi_io_files_fd_pread(i32 fd, i64 off, i32 max)`.

    One pread. The answer carries what ARRIVED, which may be fewer bytes than asked for
    and is empty at end of file; cap stays `max`, so the array destructor frees the whole
    buffer whatever len says. The descriptor's file position is untouched.
    """
    _i8, i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    pread_fn = platform_files.declare_pread(module)
    malloc_fn = declare_malloc(module)
    free_fn = declare_free(module)

    array_ty = get_byte_array_type()
    result_type = get_result_type(array_ty, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, i64, i32]),
                       name="sushi_io_files_fd_pread")
    fd, offset, maximum = func.args
    fd.name, offset.name, maximum.name = "fd", "offset", "max"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    max64 = builder.zext(maximum, i64, name="max64")
    buffer = builder.call(malloc_fn, [max64], name="pread_buf")
    is_null = builder.icmp_unsigned("==", buffer, ir.Constant(i8_ptr, None),
                                    name="alloc_failed")

    alloc_fail_bb = func.append_basic_block(name="alloc_fail")
    do_read_bb = func.append_basic_block(name="do_read")
    builder.cbranch(is_null, alloc_fail_bb, do_read_bb)

    builder.position_at_end(alloc_fail_bb)
    emit_runtime_error(module, builder, "RE2021")

    builder.position_at_end(do_read_bb)
    got = builder.call(pread_fn, [fd, buffer, max64, offset], name="got")
    ok = builder.icmp_signed(">=", got, ir.Constant(i64, 0), name="pread_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    # The tag before the free: free() overwrites errno.
    builder.position_at_end(failure_bb)
    tag = emit_file_error_tag(builder, module)
    builder.call(free_fn, [buffer])
    builder.ret(emit_err_result(builder, result_type, tag))

    builder.position_at_end(success_bb)
    descriptor = ir.Constant(array_ty, ir.Undefined)
    descriptor = builder.insert_value(descriptor,
                                      builder.trunc(got, i32, name="got32"), 0,
                                      name="desc_len")
    descriptor = builder.insert_value(descriptor, maximum, 1, name="desc_cap")
    descriptor = builder.insert_value(descriptor, buffer, 2, name="descriptor")
    builder.ret(emit_ok_result(builder, result_type, descriptor, 16))


def generate_fd_pwrite(module: ir.Module) -> None:
    """Emit `Result<i32, FileError> sushi_io_files_fd_pwrite(i32 fd, i64 off, u8[] data)`.

    One pwrite. The count it took is the Ok value; looping until the whole buffer is
    gone is the caller's job, exactly as it is for `sock_send`. The buffer belongs to
    the caller and is a borrow, so this never frees it. The file position is untouched.
    """
    _i8, _i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    pwrite_fn = platform_files.declare_pwrite(module)

    array_ty = get_byte_array_type()
    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, i64, array_ty]),
                       name="sushi_io_files_fd_pwrite")
    fd, offset, data = func.args
    fd.name, offset.name, data.name = "fd", "offset", "data"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    length = builder.extract_value(data, 0, name="data_len")
    buffer = builder.extract_value(data, 2, name="data_ptr")
    length64 = builder.zext(length, i64, name="data_len64")

    wrote = builder.call(pwrite_fn, [fd, buffer, length64, offset], name="wrote")
    ok = builder.icmp_signed(">=", wrote, ir.Constant(i64, 0), name="pwrite_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type,
                               builder.trunc(wrote, i32, name="wrote32"), 4))


def generate_fd_dup(module: ir.Module) -> None:
    """Emit `Result<i32, FileError> sushi_io_files_fd_dup(i32 fd)`.

    A SECOND descriptor over the SAME open file description. The offset is shared, so
    this is the shared-listener primitive; `fd_pread`/`fd_pwrite` are the answer for
    concurrent reads of one file.
    """
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    platform_files = get_platform_module('files')
    dup_fn = platform_files.declare_dup(module)

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32]),
                       name="sushi_io_files_fd_dup")
    fd = func.args[0]
    fd.name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    copy = builder.call(dup_fn, [fd], name="dup_fd")
    ok = builder.icmp_signed(">=", copy, ir.Constant(i32, 0), name="dup_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, copy, 4))


def generate_fd_close(module: ir.Module) -> None:
    """Emit `Result<i32, FileError> sushi_io_files_fd_close(i32 fd)`."""
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    platform_files = get_platform_module('files')
    close_fn = platform_files.declare_close(module)

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32]),
                       name="sushi_io_files_fd_close")
    fd = func.args[0]
    fd.name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    rc = builder.call(close_fn, [fd], name="close_rc")
    ok = builder.icmp_signed("==", rc, ir.Constant(i32, 0), name="close_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, ir.Constant(i32, 0), 4))
