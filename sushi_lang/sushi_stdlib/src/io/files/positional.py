"""The positional half of the descriptor layer of <io/files>: pwrite.

`fd_pread` is Sushi source (`src_sushi/io/files.sushi`).

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
from sushi_lang.backend.gep_utils import extract_stored_len
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types, get_byte_array_type, get_result_type, get_unit_enum_type,
)
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.io.files.errno import emit_errno_err_result
from sushi_lang.sushi_stdlib.src.results import emit_ok_result


def generate_ir(module: ir.Module) -> None:
    """Generate the descriptor-level primitives."""
    generate_fd_pwrite(module)


def generate_fd_pwrite(module: ir.Module) -> None:
    """Emit `Result<i64, FileError> sushi_io_files_fd_pwrite(i32 fd, i64 off, u8[] data)`.

    One pwrite. The count it took is the Ok value; looping until the whole buffer is
    gone is the caller's job, exactly as it is for `sock_send`. The buffer belongs to
    the caller and is a borrow, so this never frees it. The file position is untouched.
    """
    _i8, _i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    pwrite_fn = platform_files.declare_pwrite(module)

    array_ty = get_byte_array_type()
    result_type = get_result_type(i64, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, i64, array_ty]),
                       name="sushi_io_files_fd_pwrite")
    fd, offset, data = func.args
    fd.name, offset.name, data.name = "fd", "offset", "data"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    length = extract_stored_len(None, data, name="data_len", builder=builder)
    buffer = builder.extract_value(data, 2, name="data_ptr")

    wrote = builder.call(pwrite_fn, [fd, buffer, length, offset], name="wrote")
    ok = builder.icmp_signed(">=", wrote, ir.Constant(i64, 0), name="pwrite_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, wrote, 8))
