"""File utility functions for <io/files> module."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types, get_string_type
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.sushi_stdlib.src.string_helpers import fat_pointer_to_cstr


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
    """Generate sushi_io_files_exists(string path) -> i8.

    Uses POSIX access(path, F_OK) to check existence.
    Returns: 1 if exists, 0 otherwise
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    platform_files = get_platform_module('files')
    access_func = platform_files.declare_access(module)

    # Declare malloc and memcpy
    malloc_func = module.globals.get('malloc')
    if malloc_func is None:
        malloc_type = ir.FunctionType(i8_ptr, [i64])
        malloc_func = ir.Function(module, malloc_type, name="malloc")

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i32])

    func_type = ir.FunctionType(i8, [string_type])
    func = ir.Function(module, func_type, name="sushi_io_files_exists")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    path_arg = func.args[0]
    path_ptr = builder.extract_value(path_arg, 0, name="path_ptr")
    path_len = builder.extract_value(path_arg, 1, name="path_len")

    # Allocate buffer for null-terminated string (length + 1 for null terminator)
    len_plus_one = builder.add(path_len, ir.Constant(i32, 1), name="len_plus_one")
    buffer_size = builder.zext(len_plus_one, i64, name="buffer_size")
    null_term_path = builder.call(malloc_func, [buffer_size], name="null_term_path")

    # Copy string data to buffer
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [null_term_path, path_ptr, path_len, is_volatile])

    # Add null terminator
    null_pos = builder.gep(null_term_path, [path_len], name="null_pos")
    builder.store(ir.Constant(i8, 0), null_pos)

    f_ok = ir.Constant(i32, 0)
    result = builder.call(access_func, [null_term_path, f_ok], name="access_result")

    zero = ir.Constant(i32, 0)
    exists = builder.icmp_signed("==", result, zero, name="exists")

    result_i8 = builder.zext(exists, i8, name="result")
    builder.ret(result_i8)


def generate_is_file(module: ir.Module) -> None:
    """Generate sushi_io_files_is_file(string path) -> i8.

    Uses POSIX stat() and checks S_ISREG(st_mode).
    Returns: 1 if regular file, 0 otherwise
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    platform_files = get_platform_module('files')
    stat_func = platform_files.declare_stat(module)

    # Declare malloc and memcpy
    malloc_func = module.globals.get('malloc')
    if malloc_func is None:
        malloc_type = ir.FunctionType(i8_ptr, [i64])
        malloc_func = ir.Function(module, malloc_type, name="malloc")

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i32])

    func_type = ir.FunctionType(i8, [string_type])
    func = ir.Function(module, func_type, name="sushi_io_files_is_file")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    path_arg = func.args[0]
    path_ptr = builder.extract_value(path_arg, 0, name="path_ptr")
    path_len = builder.extract_value(path_arg, 1, name="path_len")

    # Allocate buffer for null-terminated string
    len_plus_one = builder.add(path_len, ir.Constant(i32, 1), name="len_plus_one")
    buffer_size = builder.zext(len_plus_one, i64, name="buffer_size")
    null_term_path = builder.call(malloc_func, [buffer_size], name="null_term_path")

    # Copy string data and add null terminator
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [null_term_path, path_ptr, path_len, is_volatile])
    null_pos = builder.gep(null_term_path, [path_len], name="null_pos")
    builder.store(ir.Constant(i8, 0), null_pos)

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

    platform = get_current_platform()
    mode_offset = 4 if platform.is_darwin else 24

    i16 = ir.IntType(16)
    i16_ptr = i16.as_pointer()

    i16_buffer_ptr = builder.bitcast(stat_buffer, i16_ptr)
    mode_idx = mode_offset // 2
    mode_ptr = builder.gep(i16_buffer_ptr, [ir.Constant(i32, mode_idx)], name="mode_ptr")
    st_mode_i16 = builder.load(mode_ptr, name="st_mode_i16")

    st_mode = builder.zext(st_mode_i16, i32, name="st_mode")

    S_IFMT = ir.Constant(i32, 0o170000)
    S_IFREG = ir.Constant(i32, 0o100000)

    file_type = builder.and_(st_mode, S_IFMT, name="file_type")
    is_regular = builder.icmp_signed("==", file_type, S_IFREG, name="is_regular")

    result_i8 = builder.zext(is_regular, i8, name="result")
    builder.ret(result_i8)


def generate_is_dir(module: ir.Module) -> None:
    """Generate sushi_io_files_is_dir(string path) -> i8.

    Identical to is_file() but checks S_ISDIR(st_mode).
    Returns: 1 if directory, 0 otherwise
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    platform_files = get_platform_module('files')
    stat_func = platform_files.declare_stat(module)

    # Declare malloc and memcpy
    malloc_func = module.globals.get('malloc')
    if malloc_func is None:
        malloc_type = ir.FunctionType(i8_ptr, [i64])
        malloc_func = ir.Function(module, malloc_type, name="malloc")

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i32])

    func_type = ir.FunctionType(i8, [string_type])
    func = ir.Function(module, func_type, name="sushi_io_files_is_dir")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    path_arg = func.args[0]
    path_ptr = builder.extract_value(path_arg, 0, name="path_ptr")
    path_len = builder.extract_value(path_arg, 1, name="path_len")

    # Allocate buffer for null-terminated string
    len_plus_one = builder.add(path_len, ir.Constant(i32, 1), name="len_plus_one")
    buffer_size = builder.zext(len_plus_one, i64, name="buffer_size")
    null_term_path = builder.call(malloc_func, [buffer_size], name="null_term_path")

    # Copy string data and add null terminator
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [null_term_path, path_ptr, path_len, is_volatile])
    null_pos = builder.gep(null_term_path, [path_len], name="null_pos")
    builder.store(ir.Constant(i8, 0), null_pos)

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

    platform = get_current_platform()
    mode_offset = 4 if platform.is_darwin else 24

    i16 = ir.IntType(16)
    i16_ptr = i16.as_pointer()

    i16_buffer_ptr = builder.bitcast(stat_buffer, i16_ptr)
    mode_idx = mode_offset // 2
    mode_ptr = builder.gep(i16_buffer_ptr, [ir.Constant(i32, mode_idx)], name="mode_ptr")
    st_mode_i16 = builder.load(mode_ptr, name="st_mode_i16")

    st_mode = builder.zext(st_mode_i16, i32, name="st_mode")

    S_IFMT = ir.Constant(i32, 0o170000)
    S_IFDIR = ir.Constant(i32, 0o040000)

    file_type = builder.and_(st_mode, S_IFMT, name="file_type")
    is_directory = builder.icmp_signed("==", file_type, S_IFDIR, name="is_directory")

    result_i8 = builder.zext(is_directory, i8, name="result")
    builder.ret(result_i8)


def generate_file_size(module: ir.Module) -> None:
    """Generate sushi_io_files_file_size(string path) -> Result<i64>.

    Uses POSIX stat() and returns st_size field.
    Returns: Result.Ok(size) on success, Result.Err() on failure

    Result<i64> layout: {i32 tag, [8 x i8] data}
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    platform_files = get_platform_module('files')
    stat_func = platform_files.declare_stat(module)

    # Declare malloc and memcpy
    malloc_func = module.globals.get('malloc')
    if malloc_func is None:
        malloc_type = ir.FunctionType(i8_ptr, [i64])
        malloc_func = ir.Function(module, malloc_type, name="malloc")

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i32])

    # Result<i64> = {i32 tag, [8 x i8] data}
    data_array_type = ir.ArrayType(i8, 8)
    result_type = ir.LiteralStructType([i32, data_array_type])

    func_type = ir.FunctionType(result_type, [string_type])
    func = ir.Function(module, func_type, name="sushi_io_files_file_size")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    path_arg = func.args[0]
    path_ptr = builder.extract_value(path_arg, 0, name="path_ptr")
    path_len = builder.extract_value(path_arg, 1, name="path_len")

    # Allocate buffer for null-terminated string
    len_plus_one = builder.add(path_len, ir.Constant(i32, 1), name="len_plus_one")
    buffer_size = builder.zext(len_plus_one, i64, name="buffer_size")
    null_term_path = builder.call(malloc_func, [buffer_size], name="null_term_path")

    # Copy string data and add null terminator
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [null_term_path, path_ptr, path_len, is_volatile])
    null_pos = builder.gep(null_term_path, [path_len], name="null_pos")
    builder.store(ir.Constant(i8, 0), null_pos)

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
    err_tag = ir.Constant(i32, 1)
    zero_data = ir.Constant(data_array_type, None)
    err_result = ir.Constant(result_type, ir.Undefined)
    err_result = builder.insert_value(err_result, err_tag, 0, name="err_with_tag")
    err_result = builder.insert_value(err_result, zero_data, 1, name="err_result")
    builder.ret(err_result)

    builder.position_at_end(success_bb)

    platform = get_current_platform()
    size_offset = 96 if platform.is_darwin else 48

    i64_ptr = i64.as_pointer()
    i64_buffer_ptr = builder.bitcast(stat_buffer, i64_ptr)
    size_idx = size_offset // 8
    size_ptr = builder.gep(i64_buffer_ptr, [ir.Constant(i32, size_idx)], name="size_ptr")
    st_size = builder.load(size_ptr, name="st_size")

    # Pack i64 into [8 x i8] data array
    i64_alloca = builder.alloca(i64, name="size_value")
    builder.store(st_size, i64_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")

    # Bitcast and memcpy
    src_ptr = builder.bitcast(i64_alloca, i8_ptr)
    dest_ptr = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 8)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i32])
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr, src_ptr, size_const, is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def generate_remove(module: ir.Module) -> None:
    """Generate sushi_io_files_remove(string path) -> Result<i32>.

    Uses POSIX unlink(path) to delete a file.
    Returns: Result.Ok(0) on success, Result.Err() on failure
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    platform_files = get_platform_module('files')
    unlink_func = platform_files.declare_unlink(module)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i32])

    data_array_type = ir.ArrayType(i8, 4)
    result_type = ir.LiteralStructType([i32, data_array_type])

    func_type = ir.FunctionType(result_type, [string_type])
    func = ir.Function(module, func_type, name="sushi_io_files_remove")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    path_arg = func.args[0]
    null_term_path = fat_pointer_to_cstr(module, builder, path_arg)

    result = builder.call(unlink_func, [null_term_path], name="unlink_result")

    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    err_tag = ir.Constant(i32, 1)
    zero_data = ir.Constant(data_array_type, None)
    err_result = ir.Constant(result_type, ir.Undefined)
    err_result = builder.insert_value(err_result, err_tag, 0, name="err_with_tag")
    err_result = builder.insert_value(err_result, zero_data, 1, name="err_result")
    builder.ret(err_result)

    builder.position_at_end(success_bb)

    value_alloca = builder.alloca(i32, name="value")
    builder.store(zero, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")

    src_ptr = builder.bitcast(value_alloca, i8_ptr)
    dest_ptr = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 4)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr, src_ptr, size_const, is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def generate_rmdir(module: ir.Module) -> None:
    """Generate sushi_io_files_rmdir(string path) -> Result<i32>.

    Uses POSIX rmdir(path) to remove an empty directory.
    Returns: Result.Ok(0) on success, Result.Err() on failure
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    platform_files = get_platform_module('files')
    rmdir_func = platform_files.declare_rmdir(module)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i32])

    data_array_type = ir.ArrayType(i8, 4)
    result_type = ir.LiteralStructType([i32, data_array_type])

    func_type = ir.FunctionType(result_type, [string_type])
    func = ir.Function(module, func_type, name="sushi_io_files_rmdir")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    path_arg = func.args[0]
    null_term_path = fat_pointer_to_cstr(module, builder, path_arg)

    result = builder.call(rmdir_func, [null_term_path], name="rmdir_result")

    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    err_tag = ir.Constant(i32, 1)
    zero_data = ir.Constant(data_array_type, None)
    err_result = ir.Constant(result_type, ir.Undefined)
    err_result = builder.insert_value(err_result, err_tag, 0, name="err_with_tag")
    err_result = builder.insert_value(err_result, zero_data, 1, name="err_result")
    builder.ret(err_result)

    builder.position_at_end(success_bb)

    value_alloca = builder.alloca(i32, name="value")
    builder.store(zero, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")

    src_ptr = builder.bitcast(value_alloca, i8_ptr)
    dest_ptr = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 4)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr, src_ptr, size_const, is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def generate_mkdir(module: ir.Module) -> None:
    """Generate sushi_io_files_mkdir(string path, i32 mode) -> Result<i32>.

    Uses POSIX mkdir(path, mode) to create a directory.
    Returns: Result.Ok(0) on success, Result.Err() on failure
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    platform_files = get_platform_module('files')
    mkdir_func = platform_files.declare_mkdir(module)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i32])

    data_array_type = ir.ArrayType(i8, 4)
    result_type = ir.LiteralStructType([i32, data_array_type])

    func_type = ir.FunctionType(result_type, [string_type, i32])
    func = ir.Function(module, func_type, name="sushi_io_files_mkdir")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    path_arg = func.args[0]
    mode_arg = func.args[1]

    null_term_path = fat_pointer_to_cstr(module, builder, path_arg)

    result = builder.call(mkdir_func, [null_term_path, mode_arg], name="mkdir_result")

    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    err_tag = ir.Constant(i32, 1)
    zero_data = ir.Constant(data_array_type, None)
    err_result = ir.Constant(result_type, ir.Undefined)
    err_result = builder.insert_value(err_result, err_tag, 0, name="err_with_tag")
    err_result = builder.insert_value(err_result, zero_data, 1, name="err_result")
    builder.ret(err_result)

    builder.position_at_end(success_bb)

    value_alloca = builder.alloca(i32, name="value")
    builder.store(zero, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")

    src_ptr = builder.bitcast(value_alloca, i8_ptr)
    dest_ptr = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 4)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr, src_ptr, size_const, is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def generate_rename(module: ir.Module) -> None:
    """Generate sushi_io_files_rename(string old_path, string new_path) -> Result<i32>.

    Uses POSIX rename(old, new) to rename/move a file.
    Returns: Result.Ok(0) on success, Result.Err() on failure
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    platform_files = get_platform_module('files')
    rename_func = platform_files.declare_rename(module)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i32])

    data_array_type = ir.ArrayType(i8, 4)
    result_type = ir.LiteralStructType([i32, data_array_type])

    func_type = ir.FunctionType(result_type, [string_type, string_type])
    func = ir.Function(module, func_type, name="sushi_io_files_rename")
    block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(block)

    old_path_arg = func.args[0]
    new_path_arg = func.args[1]

    old_null_term = fat_pointer_to_cstr(module, builder, old_path_arg)
    new_null_term = fat_pointer_to_cstr(module, builder, new_path_arg)

    result = builder.call(rename_func, [old_null_term, new_null_term], name="rename_result")

    zero = ir.Constant(i32, 0)
    success = builder.icmp_signed("==", result, zero, name="success")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    err_tag = ir.Constant(i32, 1)
    zero_data = ir.Constant(data_array_type, None)
    err_result = ir.Constant(result_type, ir.Undefined)
    err_result = builder.insert_value(err_result, err_tag, 0, name="err_with_tag")
    err_result = builder.insert_value(err_result, zero_data, 1, name="err_result")
    builder.ret(err_result)

    builder.position_at_end(success_bb)

    value_alloca = builder.alloca(i32, name="value")
    builder.store(zero, value_alloca)
    data_alloca = builder.alloca(data_array_type, name="data_array")

    src_ptr = builder.bitcast(value_alloca, i8_ptr)
    dest_ptr = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 4)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr, src_ptr, size_const, is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)


def generate_copy(module: ir.Module) -> None:
    """Generate sushi_io_files_copy(string src, string dst) -> Result<i32>.

    Copies file using POSIX open/read/write/close.
    Returns: Result.Ok(0) on success, Result.Err() on failure
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()

    platform_files = get_platform_module('files')
    open_func = platform_files.declare_open(module)
    read_func = platform_files.declare_read(module)
    write_func = platform_files.declare_write(module)
    close_func = platform_files.declare_close(module)

    platform = get_current_platform()
    if platform.is_darwin:
        O_RDONLY = 0
        O_WRONLY = 1
        O_CREAT = 0x0200
        O_TRUNC = 0x0400
    else:
        O_RDONLY = 0
        O_WRONLY = 1
        O_CREAT = 0x40
        O_TRUNC = 0x200

    from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc
    malloc_func = declare_malloc(module)

    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i32])

    data_array_type = ir.ArrayType(i8, 4)
    result_type = ir.LiteralStructType([i32, data_array_type])

    func_type = ir.FunctionType(result_type, [string_type, string_type])
    func = ir.Function(module, func_type, name="sushi_io_files_copy")
    entry_block = func.append_basic_block(name="entry")
    builder = ir.IRBuilder(entry_block)

    src_arg = func.args[0]
    dst_arg = func.args[1]

    src_null_term = fat_pointer_to_cstr(module, builder, src_arg)
    dst_null_term = fat_pointer_to_cstr(module, builder, dst_arg)

    src_fd = builder.call(open_func, [
        src_null_term,
        ir.Constant(i32, O_RDONLY),
        ir.Constant(i32, 0)
    ], name="src_fd")

    zero_i32 = ir.Constant(i32, 0)
    src_open_failed = builder.icmp_signed("<", src_fd, zero_i32, name="src_open_failed")

    src_open_ok_bb = func.append_basic_block(name="src_open_ok")
    error_bb = func.append_basic_block(name="error")
    builder.cbranch(src_open_failed, error_bb, src_open_ok_bb)

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

    buffer_size_i64 = ir.Constant(i64, 4096)
    copy_buffer = builder.call(malloc_func, [buffer_size_i64], name="copy_buffer")

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

    src_ptr_cast = builder.bitcast(value_alloca, i8_ptr)
    dest_ptr_cast = builder.bitcast(data_alloca, i8_ptr)
    size_const = ir.Constant(i32, 4)
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy_fn, [dest_ptr_cast, src_ptr_cast, size_const, is_volatile])

    data_value = builder.load(data_alloca, name="data_value")

    ok_tag = ir.Constant(i32, 0)
    ok_result = ir.Constant(result_type, ir.Undefined)
    ok_result = builder.insert_value(ok_result, ok_tag, 0, name="ok_with_tag")
    ok_result = builder.insert_value(ok_result, data_value, 1, name="ok_result")
    builder.ret(ok_result)

    builder.position_at_end(error_close_both_bb)
    builder.call(close_func, [src_fd])
    builder.call(close_func, [dst_fd])
    builder.branch(error_bb)

    builder.position_at_end(error_close_src_bb)
    builder.call(close_func, [src_fd])
    builder.branch(error_bb)

    builder.position_at_end(error_bb)
    err_tag = ir.Constant(i32, 1)
    zero_data = ir.Constant(data_array_type, None)
    err_result = ir.Constant(result_type, ir.Undefined)
    err_result = builder.insert_value(err_result, err_tag, 0, name="err_with_tag")
    err_result = builder.insert_value(err_result, zero_data, 1, name="err_result")
    builder.ret(err_result)
