"""IR generation for process control functions."""

from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types, get_string_type, get_result_type, get_unit_enum_type,
    get_process_output_type, get_process_output_result_type,
)
from sushi_lang.sushi_stdlib.src.string_helpers import fat_pointer_to_cstr, cstr_to_fat_pointer_with_len
from sushi_lang.sushi_stdlib.src.libc_declarations import (
    declare_malloc, declare_free, declare_strlen,
    declare_fread, declare_fseek, declare_ftell, declare_fclose,
)
from sushi_lang.sushi_stdlib.src._platform import get_platform_module


_PE_SPAWN_FAILED = 0
_PE_EXIT_FAILURE = 1
_PE_SIGNAL_RECEIVED = 2


def get_process_error_type() -> ir.LiteralStructType:
    """Get the ProcessError enum LLVM type."""
    return get_unit_enum_type()


def generate_getcwd(module: ir.Module) -> None:
    """Generate getcwd() -> Result<string, ProcessError>"""
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()
    process_error_type = get_process_error_type()
    result_type = get_result_type(string_type, process_error_type)

    platform_process = get_platform_module('process')
    libc_getcwd = platform_process.declare_getcwd(module)

    malloc_fn = declare_malloc(module)
    free_fn = declare_free(module)
    strlen_fn = declare_strlen(module)

    func_type = ir.FunctionType(result_type, [])
    func = ir.Function(module, func_type, name="sushi_getcwd")

    entry = func.append_basic_block("entry")
    success_block = func.append_basic_block("success")
    error_block = func.append_basic_block("error")

    builder = ir.IRBuilder(entry)

    path_max = ir.Constant(i64, 4096)
    buffer = builder.call(malloc_fn, [path_max])

    result_ptr = builder.call(libc_getcwd, [buffer, path_max])

    null_ptr = ir.Constant(i8_ptr, None)
    is_error = builder.icmp_unsigned('==', result_ptr, null_ptr)
    builder.cbranch(is_error, error_block, success_block)

    builder.position_at_end(success_block)
    str_len = builder.call(strlen_fn, [result_ptr])
    str_len_i32 = builder.trunc(str_len, i32)
    # owned=1: the buffer is a live malloc and the fat pointer is its sole owner, so RAII
    # must free it at scope exit. It was owned=0 (the "literal / borrow, never free" bit),
    # which leaked all 4096 bytes on every call (#177). Do NOT free it here -- the string
    # owns it now.
    sushi_string = cstr_to_fat_pointer_with_len(builder, result_ptr, str_len_i32, owned=1)

    result_ok = builder.alloca(result_type)
    tag_ptr = builder.gep(result_ok, [ir.Constant(i32, 0), ir.Constant(i32, 0)])
    builder.store(ir.Constant(i32, 0), tag_ptr)  # tag = 0 (Ok)

    data_ptr = builder.gep(result_ok, [ir.Constant(i32, 0), ir.Constant(i32, 1)])
    data_ptr_cast = builder.bitcast(data_ptr, string_type.as_pointer())
    builder.store(sushi_string, data_ptr_cast)

    result_val = builder.load(result_ok)
    builder.ret(result_val)

    builder.position_at_end(error_block)
    builder.call(free_fn, [buffer])

    result_err = builder.alloca(result_type)
    tag_ptr_err = builder.gep(result_err, [ir.Constant(i32, 0), ir.Constant(i32, 0)])
    builder.store(ir.Constant(i32, 1), tag_ptr_err)  # tag = 1 (Err)

    result_val_err = builder.load(result_err)
    builder.ret(result_val_err)


def generate_chdir(module: ir.Module) -> None:
    """Generate chdir(string path) -> Result<i32, ProcessError>"""
    i8, i8_ptr, i32, i64 = get_basic_types()
    process_error_type = get_process_error_type()
    result_type = get_result_type(i32, process_error_type)

    platform_process = get_platform_module('process')
    libc_chdir = platform_process.declare_chdir(module)

    # The path arrives already marshalled, and the CALLER frees it (#292).
    func_type = ir.FunctionType(result_type, [i8_ptr])
    func = ir.Function(module, func_type, name="sushi_chdir")
    c_path = func.args[0]
    c_path.name = "path"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    chdir_result = builder.call(libc_chdir, [c_path])

    result_ok = builder.alloca(result_type)
    tag_ptr = builder.gep(result_ok, [ir.Constant(i32, 0), ir.Constant(i32, 0)])
    builder.store(ir.Constant(i32, 0), tag_ptr)  # tag = 0 (Ok)

    data_ptr = builder.gep(result_ok, [ir.Constant(i32, 0), ir.Constant(i32, 1)])
    data_ptr_cast = builder.bitcast(data_ptr, i32.as_pointer())
    builder.store(chdir_result, data_ptr_cast)

    result_val = builder.load(result_ok)
    builder.ret(result_val)


def generate_exit(module: ir.Module) -> None:
    """Generate exit(i32 code) -> ~"""
    i8, i8_ptr, i32, i64 = get_basic_types()
    void = ir.VoidType()

    platform_process = get_platform_module('process')
    libc_exit = platform_process.declare_exit(module)

    func_type = ir.FunctionType(void, [i32])
    func = ir.Function(module, func_type, name="sushi_exit")
    code_arg = func.args[0]
    code_arg.name = "code"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call exit(code) - never returns
    builder.call(libc_exit, [code_arg])
    builder.unreachable()  # Mark as unreachable


def generate_getpid(module: ir.Module) -> None:
    """Generate getpid() -> i32"""
    i8, i8_ptr, i32, i64 = get_basic_types()

    platform_process = get_platform_module('process')
    libc_getpid = platform_process.declare_getpid(module)

    func_type = ir.FunctionType(i32, [])
    func = ir.Function(module, func_type, name="sushi_getpid")

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(libc_getpid, [])
    builder.ret(result)


def generate_getuid(module: ir.Module) -> None:
    """Generate getuid() -> i32"""
    i8, i8_ptr, i32, i64 = get_basic_types()

    platform_process = get_platform_module('process')
    libc_getuid = platform_process.declare_getuid(module)

    func_type = ir.FunctionType(i32, [])
    func = ir.Function(module, func_type, name="sushi_getuid")

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    result = builder.call(libc_getuid, [])
    builder.ret(result)


def generate_run(module: ir.Module) -> None:
    """Generate run(string cmd, string[] args) -> Result<ProcessOutput, ProcessError>."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()
    out_type = get_process_output_type()                 # {i32, string, string}
    err_type = get_process_error_type()                  # unit enum {i32, [1 x i64]}, 16 bytes
    result_type = get_process_output_result_type()       # {i32, [5 x i64]} (aligned; matches compiler)
    argv_type = ir.LiteralStructType([i32, i32, string_type.as_pointer()])  # string[]
    char_pp = i8_ptr.as_pointer()

    plat = get_platform_module('process')
    tmpfile_fn = plat.declare_tmpfile(module)
    fileno_fn = plat.declare_fileno(module)
    waitpid_fn = plat.declare_waitpid(module)
    spawnp_fn = plat.declare_posix_spawnp(module)
    fa_init_fn = plat.declare_posix_spawn_file_actions_init(module)
    fa_dup2_fn = plat.declare_posix_spawn_file_actions_adddup2(module)
    fa_destroy_fn = plat.declare_posix_spawn_file_actions_destroy(module)
    environ_g = plat.get_environ(module)

    malloc_fn = declare_malloc(module)
    free_fn = declare_free(module)
    fread_fn = declare_fread(module)
    fseek_fn = declare_fseek(module)
    ftell_fn = declare_ftell(module)
    fclose_fn = declare_fclose(module)

    func = ir.Function(module, ir.FunctionType(result_type, [string_type, argv_type]), name="sushi_run")
    cmd_arg, args_arg = func.args
    cmd_arg.name = "cmd"
    args_arg.name = "args"

    z = ir.Constant(i32, 0)
    one_i32 = ir.Constant(i32, 1)
    null_i8ptr = ir.Constant(i8_ptr, None)

    entry = func.append_basic_block("entry")
    tmpfile_fail = func.append_basic_block("tmpfile_fail")
    setup = func.append_basic_block("setup")
    argv_cond = func.append_basic_block("argv_cond")
    argv_body = func.append_basic_block("argv_body")
    argv_done = func.append_basic_block("argv_done")
    free_cond = func.append_basic_block("free_cond")
    free_body = func.append_basic_block("free_body")
    free_done = func.append_basic_block("free_done")
    spawn_err = func.append_basic_block("spawn_err")
    spawned = func.append_basic_block("spawned")
    signaled = func.append_basic_block("signaled")
    exited = func.append_basic_block("exited")

    b = ir.IRBuilder(entry)

    def emit_err(variant_tag: int) -> None:
        res = b.alloca(result_type)
        b.store(ir.Constant(i32, 1), b.gep(res, [z, z]))          # Result tag = Err
        ev = b.alloca(err_type)
        b.store(ir.Constant(i32, variant_tag), b.gep(ev, [z, z]))  # ProcessError variant tag
        # Zero the unit enum's [1 x i64] data word (#300 phase 2)
        b.store(ir.Constant(err_type.elements[1], None), b.gep(ev, [z, one_i32]))
        data = b.bitcast(b.gep(res, [z, one_i32]), err_type.as_pointer())
        b.store(b.load(ev), data)
        b.ret(b.load(res))

    def emit_read_all(f) -> ir.Value:
        b.call(fseek_fn, [f, ir.Constant(i64, 0), ir.Constant(i32, 2)])   # SEEK_END
        n64 = b.call(ftell_fn, [f])
        b.call(fseek_fn, [f, ir.Constant(i64, 0), ir.Constant(i32, 0)])   # SEEK_SET (rewind)
        buf = b.call(malloc_fn, [b.add(n64, ir.Constant(i64, 1))])
        b.call(fread_fn, [buf, ir.Constant(i64, 1), n64, f])
        b.store(ir.Constant(i8, 0), b.gep(buf, [n64]))                    # NUL terminate
        return cstr_to_fat_pointer_with_len(b, buf, b.trunc(n64, i32), owned=1)

    args_slot = b.alloca(argv_type)
    i_slot = b.alloca(i32)
    j_slot = b.alloca(i32)
    pid_slot = b.alloca(i32)
    status_slot = b.alloca(i32)
    fa_buf = b.alloca(ir.ArrayType(i8, 128))          # opaque posix_spawn_file_actions_t (conservative size)
    b.store(args_arg, args_slot)
    out_file = b.call(tmpfile_fn, [])
    err_file = b.call(tmpfile_fn, [])
    out_bad = b.icmp_unsigned('==', out_file, null_i8ptr)
    err_bad = b.icmp_unsigned('==', err_file, null_i8ptr)
    b.cbranch(b.or_(out_bad, err_bad), tmpfile_fail, setup)

    b.position_at_end(tmpfile_fail)
    emit_err(_PE_SPAWN_FAILED)

    b.position_at_end(setup)
    cmd_cstr = fat_pointer_to_cstr(module, b, cmd_arg)
    arg_len = b.load(b.gep(args_slot, [z, z]))                    # args.len
    arg_data = b.load(b.gep(args_slot, [z, ir.Constant(i32, 2)]))  # args.data : string*
    argc = b.add(arg_len, one_i32)                                # cmd + args
    slots = b.add(argc, one_i32)                                  # + NULL terminator
    argv_bytes = b.mul(b.zext(slots, i64), ir.Constant(i64, 8))
    argv_raw = b.call(malloc_fn, [argv_bytes])
    argv = b.bitcast(argv_raw, char_pp)
    b.store(cmd_cstr, b.gep(argv, [z]))                           # argv[0]
    b.store(z, i_slot)
    b.branch(argv_cond)

    b.position_at_end(argv_cond)
    i_val = b.load(i_slot)
    b.cbranch(b.icmp_signed('<', i_val, arg_len), argv_body, argv_done)

    b.position_at_end(argv_body)
    i_val = b.load(i_slot)
    elem = b.load(b.gep(arg_data, [i_val]))                       # {i8*,i32} fat pointer
    elem_cstr = fat_pointer_to_cstr(module, b, elem)
    b.store(elem_cstr, b.gep(argv, [b.add(i_val, one_i32)]))
    b.store(b.add(i_val, one_i32), i_slot)
    b.branch(argv_cond)

    b.position_at_end(argv_done)
    b.store(null_i8ptr, b.gep(argv, [argc]))                      # argv[argc] = NULL
    fa = b.bitcast(fa_buf, i8_ptr)
    b.call(fa_init_fn, [fa])
    b.call(fa_dup2_fn, [fa, b.call(fileno_fn, [out_file]), ir.Constant(i32, 1)])  # child stdout -> out_file
    b.call(fa_dup2_fn, [fa, b.call(fileno_fn, [err_file]), ir.Constant(i32, 2)])  # child stderr -> err_file
    envp = b.load(environ_g)
    rc = b.call(spawnp_fn, [pid_slot, cmd_cstr, fa, null_i8ptr, argv, envp])
    b.call(fa_destroy_fn, [fa])
    b.store(z, j_slot)
    b.branch(free_cond)

    b.position_at_end(free_cond)
    j_val = b.load(j_slot)
    b.cbranch(b.icmp_signed('<', j_val, argc), free_body, free_done)

    b.position_at_end(free_body)
    j_val = b.load(j_slot)
    b.call(free_fn, [b.load(b.gep(argv, [j_val]))])
    b.store(b.add(j_val, one_i32), j_slot)
    b.branch(free_cond)

    b.position_at_end(free_done)
    b.call(free_fn, [argv_raw])
    b.cbranch(b.icmp_signed('==', rc, z), spawned, spawn_err)

    b.position_at_end(spawn_err)
    b.call(fclose_fn, [out_file])
    b.call(fclose_fn, [err_file])
    emit_err(_PE_SPAWN_FAILED)

    b.position_at_end(spawned)
    b.call(waitpid_fn, [b.load(pid_slot), status_slot, z])
    status = b.load(status_slot)
    sig = b.and_(status, ir.Constant(i32, 0x7f))
    is_signaled = b.and_(
        b.icmp_signed('!=', sig, z),
        b.icmp_signed('!=', sig, ir.Constant(i32, 0x7f)),
    )
    b.cbranch(is_signaled, signaled, exited)

    b.position_at_end(signaled)
    b.call(fclose_fn, [out_file])
    b.call(fclose_fn, [err_file])
    emit_err(_PE_SIGNAL_RECEIVED)

    b.position_at_end(exited)
    exit_code = b.and_(b.lshr(status, ir.Constant(i32, 8)), ir.Constant(i32, 0xff))
    stdout_str = emit_read_all(out_file)
    stderr_str = emit_read_all(err_file)
    b.call(fclose_fn, [out_file])
    b.call(fclose_fn, [err_file])

    po = b.alloca(out_type)
    b.store(exit_code, b.gep(po, [z, z]))
    b.store(stdout_str, b.gep(po, [z, one_i32]))
    b.store(stderr_str, b.gep(po, [z, ir.Constant(i32, 2)]))
    po_val = b.load(po)

    res = b.alloca(result_type)
    b.store(z, b.gep(res, [z, z]))                                # Result tag = Ok
    ok_data = b.bitcast(b.gep(res, [z, one_i32]), out_type.as_pointer())
    b.store(po_val, ok_data)
    b.ret(b.load(res))
