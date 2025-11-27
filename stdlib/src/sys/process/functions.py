"""IR generation for process control functions."""

from llvmlite import ir
from stdlib.src.type_definitions import get_basic_types, get_string_type, get_result_type, get_unit_enum_type
from stdlib.src.string_helpers import fat_pointer_to_cstr, cstr_to_fat_pointer_with_len
from stdlib.src.libc_declarations import declare_malloc, declare_free, declare_strlen
from stdlib.src._platform import get_platform_module


def get_process_error_type() -> ir.LiteralStructType:
    """Get the ProcessError enum LLVM type.

    ProcessError has 3 unit variants (no associated data):
    - SpawnFailed
    - ExitFailure
    - SignalReceived

    Uses the standard unit enum type helper.
    """
    return get_unit_enum_type()


def generate_getcwd(module: ir.Module) -> None:
    """Generate getcwd() -> Result<string, ProcessError>

    Implementation:
        1. Allocate PATH_MAX (4096) byte buffer
        2. Call POSIX getcwd(buf, size)
        3. On NULL: free buffer, return Result.Err()
        4. On success: convert C string to Sushi string, return Result.Ok(string)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()
    process_error_type = get_process_error_type()
    result_type = get_result_type(string_type, process_error_type)

    # Declare platform-specific getcwd
    platform_process = get_platform_module('process')
    libc_getcwd = platform_process.declare_getcwd(module)

    # Declare helper functions
    malloc_fn = declare_malloc(module)
    free_fn = declare_free(module)
    strlen_fn = declare_strlen(module)

    # Define sushi_getcwd() -> Result<string>
    func_type = ir.FunctionType(result_type, [])
    func = ir.Function(module, func_type, name="sushi_getcwd")

    entry = func.append_basic_block("entry")
    success_block = func.append_basic_block("success")
    error_block = func.append_basic_block("error")

    builder = ir.IRBuilder(entry)

    # Allocate buffer (PATH_MAX = 4096)
    path_max = ir.Constant(i64, 4096)
    buffer = builder.call(malloc_fn, [path_max])

    # Call getcwd(buffer, size)
    result_ptr = builder.call(libc_getcwd, [buffer, path_max])

    # Check for NULL (error)
    null_ptr = ir.Constant(i8_ptr, None)
    is_error = builder.icmp_unsigned('==', result_ptr, null_ptr)
    builder.cbranch(is_error, error_block, success_block)

    # Success block: convert C string to Sushi string
    builder.position_at_end(success_block)
    str_len = builder.call(strlen_fn, [result_ptr])
    str_len_i32 = builder.trunc(str_len, i32)
    sushi_string = cstr_to_fat_pointer_with_len(builder, result_ptr, str_len_i32)

    # Note: Do NOT free the buffer - the fat pointer points to it,
    # and Sushi's RAII will handle cleanup when the string goes out of scope

    # Pack into Result.Ok
    result_ok = builder.alloca(result_type)
    tag_ptr = builder.gep(result_ok, [ir.Constant(i32, 0), ir.Constant(i32, 0)])
    builder.store(ir.Constant(i32, 0), tag_ptr)  # tag = 0 (Ok)

    data_ptr = builder.gep(result_ok, [ir.Constant(i32, 0), ir.Constant(i32, 1)])
    data_ptr_cast = builder.bitcast(data_ptr, string_type.as_pointer())
    builder.store(sushi_string, data_ptr_cast)

    result_val = builder.load(result_ok)
    builder.ret(result_val)

    # Error block: free buffer and return Result.Err()
    builder.position_at_end(error_block)
    builder.call(free_fn, [buffer])

    result_err = builder.alloca(result_type)
    tag_ptr_err = builder.gep(result_err, [ir.Constant(i32, 0), ir.Constant(i32, 0)])
    builder.store(ir.Constant(i32, 1), tag_ptr_err)  # tag = 1 (Err)

    result_val_err = builder.load(result_err)
    builder.ret(result_val_err)


def generate_chdir(module: ir.Module) -> None:
    """Generate chdir(string path) -> Result<i32, ProcessError>

    Implementation:
        1. Convert Sushi string to C string
        2. Call POSIX chdir(path)
        3. Return result wrapped in Result (0 on success, -1 on error)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()
    process_error_type = get_process_error_type()
    result_type = get_result_type(i32, process_error_type)

    # Declare platform-specific chdir
    platform_process = get_platform_module('process')
    libc_chdir = platform_process.declare_chdir(module)

    # Define sushi_chdir(string path) -> Result<i32>
    func_type = ir.FunctionType(result_type, [string_type])
    func = ir.Function(module, func_type, name="sushi_chdir")
    path_arg = func.args[0]
    path_arg.name = "path"

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Convert Sushi string to C string
    c_path = fat_pointer_to_cstr(module, builder, path_arg)

    # Call chdir(path)
    chdir_result = builder.call(libc_chdir, [c_path])

    # Pack result into Result.Ok (we return both success and error as Ok with the code)
    result_ok = builder.alloca(result_type)
    tag_ptr = builder.gep(result_ok, [ir.Constant(i32, 0), ir.Constant(i32, 0)])
    builder.store(ir.Constant(i32, 0), tag_ptr)  # tag = 0 (Ok)

    data_ptr = builder.gep(result_ok, [ir.Constant(i32, 0), ir.Constant(i32, 1)])
    data_ptr_cast = builder.bitcast(data_ptr, i32.as_pointer())
    builder.store(chdir_result, data_ptr_cast)

    result_val = builder.load(result_ok)
    builder.ret(result_val)


def generate_exit(module: ir.Module) -> None:
    """Generate exit(i32 code) -> ~

    Implementation:
        Calls libc exit() directly. Never returns.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    void = ir.VoidType()

    # Declare platform-specific exit
    platform_process = get_platform_module('process')
    libc_exit = platform_process.declare_exit(module)

    # Define sushi_exit(i32 code) -> void
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
    """Generate getpid() -> i32

    Implementation:
        Simple wrapper to POSIX getpid(). Always succeeds.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()

    # Declare platform-specific getpid
    platform_process = get_platform_module('process')
    libc_getpid = platform_process.declare_getpid(module)

    # Define sushi_getpid() -> i32
    func_type = ir.FunctionType(i32, [])
    func = ir.Function(module, func_type, name="sushi_getpid")

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call and return
    result = builder.call(libc_getpid, [])
    builder.ret(result)


def generate_getuid(module: ir.Module) -> None:
    """Generate getuid() -> i32

    Implementation:
        Simple wrapper to POSIX getuid(). Always succeeds.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()

    # Declare platform-specific getuid
    platform_process = get_platform_module('process')
    libc_getuid = platform_process.declare_getuid(module)

    # Define sushi_getuid() -> i32
    func_type = ir.FunctionType(i32, [])
    func = ir.Function(module, func_type, name="sushi_getuid")

    entry = func.append_basic_block("entry")
    builder = ir.IRBuilder(entry)

    # Call and return
    result = builder.call(libc_getuid, [])
    builder.ret(result)
