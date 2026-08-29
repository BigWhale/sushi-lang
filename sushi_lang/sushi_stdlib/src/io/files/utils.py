"""File utility functions for <io/files> module."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types, get_result_type, get_unit_enum_type,
)
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.io.files.errno import (
    emit_errno_err_result, emit_err_result, emit_file_error_tag,
)


def _declare_malloc(module: ir.Module, i8_ptr: ir.Type, i64: ir.Type) -> ir.Function:
    """Get-or-declare the libc ``malloc`` prototype in this module."""
    malloc_func = module.globals.get('malloc')
    if malloc_func is None:
        malloc_type = ir.FunctionType(i8_ptr, [i64])
        malloc_func = ir.Function(module, malloc_type, name="malloc")
    return malloc_func


def generate_ir(module: ir.Module) -> None:
    """Generate LLVM IR for file utility functions."""
    generate_read_dir(module)
    generate_mtime(module)
    generate_ctime(module)
    generate_mode(module)
    generate_is_symlink(module)
    generate_exists(module)
    generate_is_file(module)
    generate_is_dir(module)
    generate_file_size(module)
    generate_remove(module)
    generate_rename(module)
    generate_copy(module)
    generate_mkdir(module)
    generate_rmdir(module)


def generate_exists(module: ir.Module) -> None:
    """Generate sushi_io_files_exists(string path) -> i8."""
    i8, i8_ptr, i32, i64 = get_basic_types()

    platform_files = get_platform_module('files')
    access_func = platform_files.declare_access(module)



    # The path arrives already marshalled as a C string, and the CALLER frees it
    # (#292). The body used to malloc a copy here and free nothing.
    func_type = ir.FunctionType(i8, [i8_ptr])
    func = ir.Function(module, func_type, name="sushi_io_files_exists")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    null_term_path = func.args[0]

    f_ok = ir.Constant(i32, 0)
    result = builder.call(access_func, [null_term_path, f_ok], name="access_result")

    zero = ir.Constant(i32, 0)
    exists = builder.icmp_signed("==", result, zero, name="exists")

    result_i8 = builder.zext(exists, i8, name="result")
    builder.ret(result_i8)


def _generate_stat_mode_check(module: ir.Module, sushi_name: str, s_iftype: int) -> None:
    """Emit a `stat`-based predicate testing st_mode's file-type bits."""
    i8, i8_ptr, i32, i64 = get_basic_types()

    platform_files = get_platform_module('files')
    stat_func = platform_files.declare_stat(module)


    # The path arrives already marshalled as a C string, and the CALLER frees it
    # (#292). The body used to malloc a copy here and free nothing.
    func_type = ir.FunctionType(i8, [i8_ptr])
    func = ir.Function(module, func_type, name=sushi_name)
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    null_term_path = func.args[0]

    stat_buffer_type = ir.ArrayType(i8, 144)
    stat_buffer = builder.alloca(stat_buffer_type, name="stat_buffer")
    stat_buffer_ptr = builder.bitcast(stat_buffer, i8_ptr, name="stat_ptr")

    result = builder.call(stat_func, [null_term_path, stat_buffer_ptr], name="stat_result")

    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="stat_success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    false_val = ir.Constant(i8, 0)
    builder.ret(false_val)

    builder.position_at_end(success_bb)

    mode_offset = platform_files.ST_MODE_OFFSET

    i16 = ir.IntType(16)
    i16_ptr = i16.as_pointer()

    i16_buffer_ptr = builder.bitcast(stat_buffer, i16_ptr)
    mode_idx = mode_offset // 2
    mode_ptr = builder.gep(i16_buffer_ptr, [ir.Constant(i32, mode_idx)], name="mode_ptr")
    st_mode_i16 = builder.load(mode_ptr, name="st_mode_i16")

    st_mode = builder.zext(st_mode_i16, i32, name="st_mode")

    S_IFMT = ir.Constant(i32, 0o170000)
    expected = ir.Constant(i32, s_iftype)

    file_type = builder.and_(st_mode, S_IFMT, name="file_type")
    matches = builder.icmp_signed("==", file_type, expected, name="matches")

    result_i8 = builder.zext(matches, i8, name="result")
    builder.ret(result_i8)


def generate_is_file(module: ir.Module) -> None:
    """Generate sushi_io_files_is_file(string path) -> i8 (S_ISREG)."""
    _generate_stat_mode_check(module, "sushi_io_files_is_file", 0o100000)


def generate_is_dir(module: ir.Module) -> None:
    """Generate sushi_io_files_is_dir(string path) -> i8 (S_ISDIR)."""
    _generate_stat_mode_check(module, "sushi_io_files_is_dir", 0o040000)


def generate_file_size(module: ir.Module) -> None:
    """Generate sushi_io_files_file_size(string path) -> Result<i64>."""
    i8, i8_ptr, i32, i64 = get_basic_types()

    platform_files = get_platform_module('files')
    stat_func = platform_files.declare_stat(module)


    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])

    # Result<i64, FileError> = {i32 tag, [2 x i64] data} (#300 phase 2)
    result_type = get_result_type(i64, get_unit_enum_type())
    data_array_type = result_type.elements[1]

    # The path arrives already marshalled as a C string, and the CALLER frees it
    # (#292). The body used to malloc a copy here and free nothing.
    func_type = ir.FunctionType(result_type, [i8_ptr])
    func = ir.Function(module, func_type, name="sushi_io_files_file_size")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    null_term_path = func.args[0]

    stat_buffer_type = ir.ArrayType(i8, 144)
    stat_buffer = builder.alloca(stat_buffer_type, name="stat_buffer")
    stat_buffer_ptr = builder.bitcast(stat_buffer, i8_ptr, name="stat_ptr")

    result = builder.call(stat_func, [null_term_path, stat_buffer_ptr], name="stat_result")

    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="stat_success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)

    size_offset = platform_files.ST_SIZE_OFFSET

    i64_ptr = i64.as_pointer()
    i64_buffer_ptr = builder.bitcast(stat_buffer, i64_ptr)
    size_idx = size_offset // 8
    size_ptr = builder.gep(i64_buffer_ptr, [ir.Constant(i32, size_idx)], name="size_ptr")
    st_size = builder.load(size_ptr, name="st_size")

    i64_alloca = builder.alloca(i64, name="size_value")
    builder.store(st_size, i64_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")
    builder.store(ir.Constant(data_array_type, None), data_alloca)

    src_ptr = builder.bitcast(i64_alloca, i8_ptr)
    dest_ptr = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 8)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr, src_ptr, builder.zext(size_const, ir.IntType(64)), is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def generate_remove(module: ir.Module) -> None:
    """Generate sushi_io_files_remove(string path) -> Result<i32>."""
    i8, i8_ptr, i32, i64 = get_basic_types()

    platform_files = get_platform_module('files')
    unlink_func = platform_files.declare_unlink(module)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])

    # Result<i32, FileError> = {i32 tag, [2 x i64] data} (#300 phase 2):
    # FileError is a unit enum {i32, [1 x i64]} = 16 bytes, so K = max(4, 16)/8 = 2
    result_type = get_result_type(i32, get_unit_enum_type())
    data_array_type = result_type.elements[1]

    # The path arrives already marshalled as a C string, and the CALLER frees it
    # (#292). The body used to malloc a copy here and free nothing.
    func_type = ir.FunctionType(result_type, [i8_ptr])
    func = ir.Function(module, func_type, name="sushi_io_files_remove")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    null_term_path = func.args[0]

    result = builder.call(unlink_func, [null_term_path], name="unlink_result")

    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)

    value_alloca = builder.alloca(i32, name="value")
    builder.store(zero, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")
    builder.store(ir.Constant(data_array_type, None), data_alloca)

    src_ptr = builder.bitcast(value_alloca, i8_ptr)
    dest_ptr = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 4)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr, src_ptr, builder.zext(size_const, ir.IntType(64)), is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def generate_rmdir(module: ir.Module) -> None:
    """Generate sushi_io_files_rmdir(string path) -> Result<i32>."""
    i8, i8_ptr, i32, i64 = get_basic_types()

    platform_files = get_platform_module('files')
    rmdir_func = platform_files.declare_rmdir(module)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])

    # Result<i32, FileError> = {i32 tag, [2 x i64] data} (#300 phase 2):
    # FileError is a unit enum {i32, [1 x i64]} = 16 bytes, so K = max(4, 16)/8 = 2
    result_type = get_result_type(i32, get_unit_enum_type())
    data_array_type = result_type.elements[1]

    # The path arrives already marshalled as a C string, and the CALLER frees it
    # (#292). The body used to malloc a copy here and free nothing.
    func_type = ir.FunctionType(result_type, [i8_ptr])
    func = ir.Function(module, func_type, name="sushi_io_files_rmdir")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    null_term_path = func.args[0]

    result = builder.call(rmdir_func, [null_term_path], name="rmdir_result")

    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)

    value_alloca = builder.alloca(i32, name="value")
    builder.store(zero, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")
    builder.store(ir.Constant(data_array_type, None), data_alloca)

    src_ptr = builder.bitcast(value_alloca, i8_ptr)
    dest_ptr = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 4)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr, src_ptr, builder.zext(size_const, ir.IntType(64)), is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def generate_mkdir(module: ir.Module) -> None:
    """Generate sushi_io_files_mkdir(string path, i32 mode) -> Result<i32>."""
    i8, i8_ptr, i32, i64 = get_basic_types()

    platform_files = get_platform_module('files')
    mkdir_func = platform_files.declare_mkdir(module)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])

    # Result<i32, FileError> = {i32 tag, [2 x i64] data} (#300 phase 2):
    # FileError is a unit enum {i32, [1 x i64]} = 16 bytes, so K = max(4, 16)/8 = 2
    result_type = get_result_type(i32, get_unit_enum_type())
    data_array_type = result_type.elements[1]

    # The path arrives already marshalled as a C string, and the CALLER frees it
    # (#292). The body used to malloc a copy here and free nothing.
    func_type = ir.FunctionType(result_type, [i8_ptr, i32])
    func = ir.Function(module, func_type, name="sushi_io_files_mkdir")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    null_term_path = func.args[0]
    mode_arg = func.args[1]

    result = builder.call(mkdir_func, [null_term_path, mode_arg], name="mkdir_result")

    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)

    value_alloca = builder.alloca(i32, name="value")
    builder.store(zero, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")
    builder.store(ir.Constant(data_array_type, None), data_alloca)

    src_ptr = builder.bitcast(value_alloca, i8_ptr)
    dest_ptr = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 4)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr, src_ptr, builder.zext(size_const, ir.IntType(64)), is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def generate_rename(module: ir.Module) -> None:
    """Generate sushi_io_files_rename(string old_path, string new_path) -> Result<i32>."""
    i8, i8_ptr, i32, i64 = get_basic_types()

    platform_files = get_platform_module('files')
    rename_func = platform_files.declare_rename(module)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])

    # Result<i32, FileError> = {i32 tag, [2 x i64] data} (#300 phase 2):
    # FileError is a unit enum {i32, [1 x i64]} = 16 bytes, so K = max(4, 16)/8 = 2
    result_type = get_result_type(i32, get_unit_enum_type())
    data_array_type = result_type.elements[1]

    # The path arrives already marshalled as a C string, and the CALLER frees it
    # (#292). The body used to malloc a copy here and free nothing.
    func_type = ir.FunctionType(result_type, [i8_ptr, i8_ptr])
    func = ir.Function(module, func_type, name="sushi_io_files_rename")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    old_null_term = func.args[0]
    new_null_term = func.args[1]

    result = builder.call(rename_func, [old_null_term, new_null_term], name="rename_result")

    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)

    value_alloca = builder.alloca(i32, name="value")
    builder.store(zero, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")
    builder.store(ir.Constant(data_array_type, None), data_alloca)

    src_ptr = builder.bitcast(value_alloca, i8_ptr)
    dest_ptr = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 4)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr, src_ptr, builder.zext(size_const, ir.IntType(64)), is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


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

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])

    COPY_BUFFER_BYTES = 4096

    # Result<i32, FileError> = {i32 tag, [2 x i64] data} (#300 phase 2):
    # FileError is a unit enum {i32, [1 x i64]} = 16 bytes, so K = max(4, 16)/8 = 2
    result_type = get_result_type(i32, get_unit_enum_type())
    data_array_type = result_type.elements[1]

    # The path arrives already marshalled as a C string, and the CALLER frees it
    # (#292). The body used to malloc a copy here and free nothing.
    func_type = ir.FunctionType(result_type, [i8_ptr, i8_ptr])
    func = ir.Function(module, func_type, name="sushi_io_files_copy")
    entry_block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(entry_block)

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

    value_alloca = builder.alloca(i32, name="value")
    builder.store(zero_i32, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")
    builder.store(ir.Constant(data_array_type, None), data_alloca)

    src_ptr_cast = builder.bitcast(value_alloca, i8_ptr)
    dest_ptr_cast = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 4)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr_cast, src_ptr_cast, builder.zext(size_const, ir.IntType(64)), is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)

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


def generate_read_dir(module: ir.Module) -> None:
    """Generate sushi_io_files_read_dir(string path) -> Result<string[]>.

    One opendir/readdir walk. Entry names only, in readdir order; "." and ".."
    are skipped. Each name is an owned string; the descriptor and the names are
    the caller's to free (RAII reaches them through the Result payload).
    """
    from sushi_lang.sushi_stdlib.src.type_definitions import (
        get_dynamic_array_type, get_string_type,
    )
    from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
    from sushi_lang.sushi_stdlib.src.libc_declarations import (
        declare_realloc, declare_strlen,
    )

    i8, i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    opendir_func = platform_files.declare_opendir(module)
    readdir_func = platform_files.declare_readdir(module)
    closedir_func = platform_files.declare_closedir(module)
    malloc_func = _declare_malloc(module, i8_ptr, i64)
    realloc_func = declare_realloc(module)
    strlen_func = declare_strlen(module)
    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])

    string_type = get_string_type()
    string_ptr = string_type.as_pointer()
    array_type = get_dynamic_array_type(string_type)
    STRING_STRIDE = 16  # {i8*, i32, i8} padded to 16; string_split uses the same stride

    result_type = get_result_type(array_type, get_unit_enum_type())
    data_array_type = result_type.elements[1]

    # The path arrives already marshalled as a C string, and the CALLER frees it (#292).
    func_type = ir.FunctionType(result_type, [i8_ptr])
    func = ir.Function(module, func_type, name="sushi_io_files_read_dir")
    entry_bb = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(entry_bb)

    null_term_path = func.args[0]

    dir_handle = builder.call(opendir_func, [null_term_path], name="dir_handle")
    null_ptr = ir.Constant(i8_ptr, None)
    open_failed = builder.icmp_unsigned("==", dir_handle, null_ptr, name="open_failed")

    init_bb = func.append_basic_block(name="init")
    err_bb = func.append_basic_block(name="err")
    builder.cbranch(open_failed, err_bb, init_bb)

    builder.position_at_end(err_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(init_bb)
    len_slot = builder.alloca(i32, name="len_slot")
    cap_slot = builder.alloca(i32, name="cap_slot")
    data_slot = builder.alloca(string_ptr, name="data_slot")
    zero_i32 = ir.Constant(i32, 0)
    initial_cap = ir.Constant(i32, 8)
    builder.store(zero_i32, len_slot)
    builder.store(initial_cap, cap_slot)
    initial_bytes = ir.Constant(i64, 8 * STRING_STRIDE)
    initial_data = builder.call(malloc_func, [initial_bytes], name="initial_data")
    builder.store(builder.bitcast(initial_data, string_ptr), data_slot)

    loop_bb = func.append_basic_block(name="loop")
    builder.branch(loop_bb)

    builder.position_at_end(loop_bb)
    entry_ptr = builder.call(readdir_func, [dir_handle], name="dirent")
    at_end = builder.icmp_unsigned("==", entry_ptr, null_ptr, name="at_end")

    check_bb = func.append_basic_block(name="check_name")
    done_bb = func.append_basic_block(name="done")
    builder.cbranch(at_end, done_bb, check_bb)

    # Skip "." and "..": name[0] == '.' and (name[1] == 0 or (name[1] == '.' and name[2] == 0)).
    builder.position_at_end(check_bb)
    name_ptr = builder.gep(entry_ptr, [ir.Constant(i32, platform_files.DIRENT_NAME_OFFSET)],
                           name="name_ptr")
    dot = ir.Constant(i8, ord('.'))
    nul = ir.Constant(i8, 0)
    c0 = builder.load(name_ptr, name="c0")
    starts_dot = builder.icmp_unsigned("==", c0, dot, name="starts_dot")

    keep_bb = func.append_basic_block(name="keep")
    check1_bb = func.append_basic_block(name="check_c1")
    builder.cbranch(starts_dot, check1_bb, keep_bb)

    builder.position_at_end(check1_bb)
    c1_ptr = builder.gep(name_ptr, [ir.Constant(i32, 1)], name="c1_ptr")
    c1 = builder.load(c1_ptr, name="c1")
    is_dot_entry = builder.icmp_unsigned("==", c1, nul, name="is_dot_entry")
    check2_bb = func.append_basic_block(name="check_c2")
    builder.cbranch(is_dot_entry, loop_bb, check2_bb)

    builder.position_at_end(check2_bb)
    second_dot = builder.icmp_unsigned("==", c1, dot, name="second_dot")
    check3_bb = func.append_basic_block(name="check_c3")
    builder.cbranch(second_dot, check3_bb, keep_bb)

    builder.position_at_end(check3_bb)
    c2_ptr = builder.gep(name_ptr, [ir.Constant(i32, 2)], name="c2_ptr")
    c2 = builder.load(c2_ptr, name="c2")
    is_dotdot_entry = builder.icmp_unsigned("==", c2, nul, name="is_dotdot_entry")
    builder.cbranch(is_dotdot_entry, loop_bb, keep_bb)

    # Copy the name into an owned string and append it.
    builder.position_at_end(keep_bb)
    name_len = builder.call(strlen_func, [name_ptr], name="name_len")
    name_len_i64 = builder.zext(name_len, i64, name="name_len_i64")
    name_buf = builder.call(malloc_func, [name_len_i64], name="name_buf")
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [name_buf, name_ptr, name_len_i64, is_volatile])
    name_string = cstr_to_fat_pointer_with_len(builder, name_buf, name_len, owned=1)

    cur_len = builder.load(len_slot, name="cur_len")
    cur_cap = builder.load(cap_slot, name="cur_cap")
    is_full = builder.icmp_signed("==", cur_len, cur_cap, name="is_full")

    grow_bb = func.append_basic_block(name="grow")
    store_bb = func.append_basic_block(name="store")
    builder.cbranch(is_full, grow_bb, store_bb)

    builder.position_at_end(grow_bb)
    new_cap = builder.mul(cur_cap, ir.Constant(i32, 2), name="new_cap")
    new_bytes = builder.mul(builder.zext(new_cap, i64), ir.Constant(i64, STRING_STRIDE),
                            name="new_bytes")
    old_data = builder.load(data_slot, name="old_data")
    new_data = builder.call(realloc_func,
                            [builder.bitcast(old_data, i8_ptr), new_bytes], name="new_data")
    builder.store(builder.bitcast(new_data, string_ptr), data_slot)
    builder.store(new_cap, cap_slot)
    builder.branch(store_bb)

    builder.position_at_end(store_bb)
    data = builder.load(data_slot, name="data")
    elem_ptr = builder.gep(data, [cur_len], name="elem_ptr")
    builder.store(name_string, elem_ptr)
    builder.store(builder.add(cur_len, ir.Constant(i32, 1)), len_slot)
    builder.branch(loop_bb)

    # Wrap the descriptor {len, cap, data} into Result.Ok.
    builder.position_at_end(done_bb)
    builder.call(closedir_func, [dir_handle])

    final_len = builder.load(len_slot, name="final_len")
    final_cap = builder.load(cap_slot, name="final_cap")
    final_data = builder.load(data_slot, name="final_data")
    descriptor = ir.Constant(array_type, ir.Undefined)
    descriptor = builder.insert_value(descriptor, final_len, 0, name="desc_len")
    descriptor = builder.insert_value(descriptor, final_cap, 1, name="desc_cap")
    descriptor = builder.insert_value(descriptor, final_data, 2, name="descriptor")

    desc_alloca = builder.alloca(array_type, name="desc_alloca")
    builder.store(descriptor, desc_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")
    builder.store(ir.Constant(data_array_type, None), data_alloca)
    builder.call(memcpy_fn, [
        builder.bitcast(data_alloca, i8_ptr),
        builder.bitcast(desc_alloca, i8_ptr),
        ir.Constant(i64, 16), is_volatile,
    ])
    data_value = builder.load(data_alloca, name="data_value")

    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, zero_i32, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def _generate_stat_field(module: ir.Module, sushi_name: str, ok_type: ir.Type,
                         ok_bytes: int, use_lstat: bool, read_field) -> None:
    """Emit `Result<T> <sushi_name>(i8* path)`: one stat/lstat read, one field.

    `read_field(builder, stat_buffer, platform_files)` loads the field and
    returns it as `ok_type`. A failed call maps errno to the FileError.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    stat_func = (platform_files.declare_lstat(module) if use_lstat
                 else platform_files.declare_stat(module))
    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])

    result_type = get_result_type(ok_type, get_unit_enum_type())
    data_array_type = result_type.elements[1]

    # The path arrives already marshalled as a C string, and the CALLER frees it (#292).
    func_type = ir.FunctionType(result_type, [i8_ptr])
    func = ir.Function(module, func_type, name=sushi_name)
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    null_term_path = func.args[0]

    stat_buffer_type = ir.ArrayType(i8, 144)
    stat_buffer = builder.alloca(stat_buffer_type, name="stat_buffer")
    stat_buffer_ptr = builder.bitcast(stat_buffer, i8_ptr, name="stat_ptr")

    result = builder.call(stat_func, [null_term_path, stat_buffer_ptr], name="stat_result")
    success = builder.icmp_signed("==", result, ir.Constant(i32, 0), name="stat_success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    value = read_field(builder, stat_buffer, platform_files)

    value_alloca = builder.alloca(ok_type, name="field_value")
    builder.store(value, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")
    builder.store(ir.Constant(data_array_type, None), data_alloca)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [
        builder.bitcast(data_alloca, i8_ptr),
        builder.bitcast(value_alloca, i8_ptr),
        ir.Constant(i64, ok_bytes), is_volatile,
    ])
    data_value = builder.load(data_alloca, name="data_value")

    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ir.Constant(i32, 0), 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def _read_i64_at(offset_name: str):
    """A field reader for an i64 stat field at a platform-named offset."""
    def read(builder, stat_buffer, platform_files):
        i32 = ir.IntType(32)
        i64 = ir.IntType(64)
        buf = builder.bitcast(stat_buffer, i64.as_pointer())
        idx = getattr(platform_files, offset_name) // 8
        ptr = builder.gep(buf, [ir.Constant(i32, idx)], name="field_ptr")
        return builder.load(ptr, name="field")
    return read


def _read_mode(builder, stat_buffer, platform_files):
    """st_mode as i32. The field is u16 on Darwin and u32 on glibc; the value
    fits 16 bits, so a little-endian i16 read serves both (the is_file path
    reads it the same way)."""
    i16 = ir.IntType(16)
    i32 = ir.IntType(32)
    buf = builder.bitcast(stat_buffer, i16.as_pointer())
    idx = platform_files.ST_MODE_OFFSET // 2
    ptr = builder.gep(buf, [ir.Constant(i32, idx)], name="mode_ptr")
    return builder.zext(builder.load(ptr, name="mode_i16"), i32, name="mode")


def generate_mtime(module: ir.Module) -> None:
    """Generate sushi_io_files_mtime(string path) -> Result<i64> (unix seconds)."""
    i64 = ir.IntType(64)
    _generate_stat_field(module, "sushi_io_files_mtime", i64, 8, False,
                         _read_i64_at("ST_MTIME_OFFSET"))


def generate_ctime(module: ir.Module) -> None:
    """Generate sushi_io_files_ctime(string path) -> Result<i64> (unix seconds)."""
    i64 = ir.IntType(64)
    _generate_stat_field(module, "sushi_io_files_ctime", i64, 8, False,
                         _read_i64_at("ST_CTIME_OFFSET"))


def generate_mode(module: ir.Module) -> None:
    """Generate sushi_io_files_mode(string path) -> Result<i32> (raw st_mode)."""
    i32 = ir.IntType(32)
    _generate_stat_field(module, "sushi_io_files_mode", i32, 4, False, _read_mode)


def generate_is_symlink(module: ir.Module) -> None:
    """Generate sushi_io_files_is_symlink(string path) -> Result<bool>, via lstat."""
    i8 = ir.IntType(8)

    def read(builder, stat_buffer, platform_files):
        i32 = ir.IntType(32)
        mode = _read_mode(builder, stat_buffer, platform_files)
        file_type = builder.and_(mode, ir.Constant(i32, 0o170000), name="file_type")
        is_link = builder.icmp_signed("==", file_type, ir.Constant(i32, 0o120000),
                                      name="is_link")
        return builder.zext(is_link, i8, name="is_link_i8")

    _generate_stat_field(module, "sushi_io_files_is_symlink", i8, 1, True, read)
