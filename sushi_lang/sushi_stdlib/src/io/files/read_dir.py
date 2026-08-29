"""The directory listing of <io/files>: one opendir/readdir walk."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types, get_result_type, get_unit_enum_type,
    get_dynamic_array_type, get_string_type,
)
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
from sushi_lang.sushi_stdlib.src.libc_declarations import (
    declare_malloc, declare_realloc, declare_strlen,
)
from sushi_lang.sushi_stdlib.src.io.files.errno import emit_errno_err_result
from sushi_lang.sushi_stdlib.src.io.files.results import emit_ok_result


def generate_ir(module: ir.Module) -> None:
    """Generate LLVM IR for the directory listing."""
    generate_read_dir(module)


def generate_read_dir(module: ir.Module) -> None:
    """Generate sushi_io_files_read_dir(string path) -> Result<string[]>.

    One opendir/readdir walk. Entry names only, in readdir order; "." and ".."
    are skipped. Each name is an owned string; the descriptor and the names are
    the caller's to free (RAII reaches them through the Result payload).
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    opendir_func = platform_files.declare_opendir(module)
    readdir_func = platform_files.declare_readdir(module)
    closedir_func = platform_files.declare_closedir(module)
    malloc_func = declare_malloc(module)
    realloc_func = declare_realloc(module)
    strlen_func = declare_strlen(module)
    memcpy_fn = module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])

    string_type = get_string_type()
    string_ptr = string_type.as_pointer()
    array_type = get_dynamic_array_type(string_type)
    STRING_STRIDE = 16  # {i8*, i32, i8} padded to 16; string_split uses the same stride

    result_type = get_result_type(array_type, get_unit_enum_type())

    # The path arrives already marshalled as a C string, and the CALLER frees it (#292).
    func = ir.Function(module, ir.FunctionType(result_type, [i8_ptr]),
                       name="sushi_io_files_read_dir")
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

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

    descriptor = ir.Constant(array_type, ir.Undefined)
    descriptor = builder.insert_value(descriptor, builder.load(len_slot), 0, name="desc_len")
    descriptor = builder.insert_value(descriptor, builder.load(cap_slot), 1, name="desc_cap")
    descriptor = builder.insert_value(descriptor, builder.load(data_slot), 2, name="descriptor")
    builder.ret(emit_ok_result(builder, result_type, descriptor, 16))
