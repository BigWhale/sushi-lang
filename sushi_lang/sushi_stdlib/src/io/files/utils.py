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
