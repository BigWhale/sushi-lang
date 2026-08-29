"""The file copy of <io/files>: an open/read/write loop over two descriptors."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types, get_result_type, get_unit_enum_type,
)
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.io.files.errno import emit_file_error_tag
from sushi_lang.sushi_stdlib.src.io.files.results import emit_ok_result, emit_err_result


def generate_ir(module: ir.Module) -> None:
    """Generate LLVM IR for the file copy."""
    generate_copy(module)


def generate_copy(module: ir.Module) -> None:
    """Generate sushi_io_files_copy(string src, string dst) -> Result<i32>."""
    i8, i8_ptr, i32, i64 = get_basic_types()

    platform_files = get_platform_module('files')
    open_func = platform_files.declare_open(module)
    read_func = platform_files.declare_read(module)
    write_func = platform_files.declare_write(module)
    close_func = platform_files.declare_close(module)

    O_RDONLY = platform_files.O_RDONLY
    O_WRONLY = platform_files.O_WRONLY
    O_CREAT = platform_files.O_CREAT
    O_TRUNC = platform_files.O_TRUNC

    COPY_BUFFER_BYTES = 4096

    # Result<i32, FileError> = {i32 tag, [2 x i64] data} (#300 phase 2):
    # FileError is a unit enum {i32, [1 x i64]} = 16 bytes, so K = max(4, 16)/8 = 2
    result_type = get_result_type(i32, get_unit_enum_type())

    # The paths arrive already marshalled as C strings, and the CALLER frees them (#292).
    func = ir.Function(module, ir.FunctionType(result_type, [i8_ptr, i8_ptr]),
                       name="sushi_io_files_copy")
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    src_null_term = func.args[0]
    dst_null_term = func.args[1]

    # The transfer buffer has purely local lifetime, so it is a LOCAL. It used to be a
    # malloc that no exit path freed, and this function has two `ret` instructions reached
    # by four routes -- a per-exit free is one missed branch away from a leak and one
    # doubled branch away from a double free (#291).
    copy_buffer = builder.alloca(ir.ArrayType(i8, COPY_BUFFER_BYTES), name="copy_buffer_local")
    copy_buffer = builder.bitcast(copy_buffer, i8_ptr, name="copy_buffer")

    # errno is read on the failure edge and parked here, BEFORE any close()
    # call can overwrite it; the shared error block loads it back.
    err_tag_slot = builder.alloca(i32, name="err_tag_slot")

    src_fd = builder.call(open_func, [
        src_null_term,
        ir.Constant(i32, O_RDONLY),
        ir.Constant(i32, 0)
    ], name="src_fd")

    zero_i32 = ir.Constant(i32, 0)
    src_open_failed = builder.icmp_signed("<", src_fd, zero_i32, name="src_open_failed")

    src_open_ok_bb = func.append_basic_block(name="src_open_ok")
    error_bb = func.append_basic_block(name="error")
    error_src_open_bb = func.append_basic_block(name="error_src_open")
    builder.cbranch(src_open_failed, error_src_open_bb, src_open_ok_bb)

    builder.position_at_end(error_src_open_bb)
    builder.store(emit_file_error_tag(builder, module), err_tag_slot)
    builder.branch(error_bb)

    builder.position_at_end(src_open_ok_bb)
    dst_flags = O_WRONLY | O_CREAT | O_TRUNC
    dst_mode = 0o644
    dst_fd = builder.call(open_func, [
        dst_null_term,
        ir.Constant(i32, dst_flags),
        ir.Constant(i32, dst_mode)
    ], name="dst_fd")

    dst_open_failed = builder.icmp_signed("<", dst_fd, zero_i32, name="dst_open_failed")

    dst_open_ok_bb = func.append_basic_block(name="dst_open_ok")
    error_close_src_bb = func.append_basic_block(name="error_close_src")
    builder.cbranch(dst_open_failed, error_close_src_bb, dst_open_ok_bb)

    builder.position_at_end(dst_open_ok_bb)

    buffer_size_i64 = ir.Constant(i64, COPY_BUFFER_BYTES)

    loop_bb = func.append_basic_block(name="copy_loop")
    builder.branch(loop_bb)

    builder.position_at_end(loop_bb)

    bytes_read = builder.call(read_func, [src_fd, copy_buffer, buffer_size_i64], name="bytes_read")

    zero_i64 = ir.Constant(i64, 0)
    read_error = builder.icmp_signed("<", bytes_read, zero_i64, name="read_error")
    eof = builder.icmp_signed("==", bytes_read, zero_i64, name="eof")

    read_ok_bb = func.append_basic_block(name="read_ok")
    error_close_both_bb = func.append_basic_block(name="error_close_both")
    success_close_bb = func.append_basic_block(name="success_close")

    builder.cbranch(read_error, error_close_both_bb, read_ok_bb)

    builder.position_at_end(read_ok_bb)
    write_data_bb = func.append_basic_block(name="write_data")
    builder.cbranch(eof, success_close_bb, write_data_bb)

    builder.position_at_end(write_data_bb)
    bytes_written = builder.call(write_func, [dst_fd, copy_buffer, bytes_read], name="bytes_written")

    write_error = builder.icmp_signed("!=", bytes_written, bytes_read, name="write_error")
    builder.cbranch(write_error, error_close_both_bb, loop_bb)

    builder.position_at_end(success_close_bb)
    builder.call(close_func, [src_fd])
    builder.call(close_func, [dst_fd])
    builder.ret(emit_ok_result(builder, result_type, zero_i32, 4))

    builder.position_at_end(error_close_both_bb)
    builder.store(emit_file_error_tag(builder, module), err_tag_slot)
    builder.call(close_func, [src_fd])
    builder.call(close_func, [dst_fd])
    builder.branch(error_bb)

    builder.position_at_end(error_close_src_bb)
    builder.store(emit_file_error_tag(builder, module), err_tag_slot)
    builder.call(close_func, [src_fd])
    builder.branch(error_bb)

    builder.position_at_end(error_bb)
    err_tag = builder.load(err_tag_slot, name="err_tag")
    builder.ret(emit_err_result(builder, result_type, err_tag))
