"""The stat readers of <io/files>: the type predicates and the per-field reads.

One preamble (_emit_stat_call) allocates the opaque buffer and makes the call;
one frame (_generate_stat_field) wraps a field read into a Result. A predicate
returns bare i8 with false on failure and shares only the preamble.

The buffer is 144 bytes: sizeof(struct stat) is 144 on macOS and on glibc
x86_64, 128 on glibc aarch64 (probe-verified 2026-08-29). Field offsets are
platform constants next to the declarations in _platform/{darwin,linux}.
"""
from typing import Callable
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types, get_result_type, get_unit_enum_type,
)
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.io.files.errno import emit_errno_err_result
from sushi_lang.sushi_stdlib.src.io.files.results import emit_ok_result


def generate_ir(module: ir.Module) -> None:
    """Generate LLVM IR for the stat-backed functions."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    _generate_stat_mode_check(module, "sushi_io_files_is_file", 0o100000)
    _generate_stat_mode_check(module, "sushi_io_files_is_dir", 0o040000)
    _generate_stat_field(module, "sushi_io_files_file_size", i64, 8, False,
                         _read_i64_at("ST_SIZE_OFFSET"))
    _generate_stat_field(module, "sushi_io_files_mtime", i64, 8, False,
                         _read_i64_at("ST_MTIME_OFFSET"))
    _generate_stat_field(module, "sushi_io_files_ctime", i64, 8, False,
                         _read_i64_at("ST_CTIME_OFFSET"))
    _generate_stat_field(module, "sushi_io_files_mode", i32, 4, False, _read_mode)
    _generate_is_symlink(module)


def _emit_stat_call(builder: ir.IRBuilder, stat_func: ir.Function,
                    path: ir.Value) -> tuple:
    """Alloca the opaque stat buffer, make the call; answer (success_i1, buffer)."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    stat_buffer = builder.alloca(ir.ArrayType(i8, 144), name="stat_buffer")
    stat_buffer_ptr = builder.bitcast(stat_buffer, i8_ptr, name="stat_ptr")
    result = builder.call(stat_func, [path, stat_buffer_ptr], name="stat_result")
    success = builder.icmp_signed("==", result, ir.Constant(i32, 0), name="stat_success")
    return success, stat_buffer


def _generate_stat_mode_check(module: ir.Module, sushi_name: str, s_iftype: int) -> None:
    """Emit `i8 <sushi_name>(i8* path)`: a stat-based st_mode type-bit predicate.

    Bare i8, false on any failure: "not a file" and "no such path" are the
    same answer to this question.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    stat_func = platform_files.declare_stat(module)

    # The path arrives already marshalled as a C string, and the CALLER frees it (#292).
    func = ir.Function(module, ir.FunctionType(i8, [i8_ptr]), name=sushi_name)
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    success, stat_buffer = _emit_stat_call(builder, stat_func, func.args[0])
    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(ir.Constant(i8, 0))

    builder.position_at_end(success_bb)
    st_mode = _read_mode(builder, stat_buffer, platform_files)
    file_type = builder.and_(st_mode, ir.Constant(i32, 0o170000), name="file_type")
    matches = builder.icmp_signed("==", file_type, ir.Constant(i32, s_iftype),
                                  name="matches")
    builder.ret(builder.zext(matches, i8, name="result"))


def _generate_stat_field(module: ir.Module, sushi_name: str, ok_type: ir.Type,
                         ok_bytes: int, use_lstat: bool,
                         read_field: Callable) -> None:
    """Emit `Result<T> <sushi_name>(i8* path)`: one stat/lstat read, one field.

    `read_field(builder, stat_buffer, platform_files)` loads the field and
    returns it as `ok_type`. A failed call maps errno to the FileError.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    stat_func = (platform_files.declare_lstat(module) if use_lstat
                 else platform_files.declare_stat(module))

    result_type = get_result_type(ok_type, get_unit_enum_type())

    # The path arrives already marshalled as a C string, and the CALLER frees it (#292).
    func = ir.Function(module, ir.FunctionType(result_type, [i8_ptr]), name=sushi_name)
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    success, stat_buffer = _emit_stat_call(builder, stat_func, func.args[0])
    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(success, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    value = read_field(builder, stat_buffer, platform_files)
    builder.ret(emit_ok_result(builder, result_type, value, ok_bytes))


def _read_i64_at(offset_name: str) -> Callable:
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
    fits 16 bits, so a little-endian i16 read serves both."""
    i16 = ir.IntType(16)
    i32 = ir.IntType(32)
    buf = builder.bitcast(stat_buffer, i16.as_pointer())
    idx = platform_files.ST_MODE_OFFSET // 2
    ptr = builder.gep(buf, [ir.Constant(i32, idx)], name="mode_ptr")
    return builder.zext(builder.load(ptr, name="mode_i16"), i32, name="mode")


def _generate_is_symlink(module: ir.Module) -> None:
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
