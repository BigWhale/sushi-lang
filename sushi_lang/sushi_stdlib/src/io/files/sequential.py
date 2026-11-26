"""The SEQUENTIAL half of the descriptor layer: the string write.

`fd_read`, `fd_readln` and `fd_write` are Sushi source (`src_sushi/io/files.sushi`).

The design record is `docs/design/stdlib-syscall-layer.md`.
"""
from llvmlite import ir
from sushi_lang.backend.string_layout import string_size_i64
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types, get_result_type, get_string_type,
    get_unit_enum_type,
)
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.io.files.errno import emit_errno_err_result, emit_is_eintr
from sushi_lang.sushi_stdlib.src.results import emit_ok_result
from sushi_lang.backend.memory.allocas import entry_alloca


def generate_ir(module: ir.Module) -> None:
    """Generate the string write of the descriptor layer."""
    generate_fd_write_str(module)


def _emit_write_all(builder: ir.IRBuilder, func: ir.Function, write_fn: ir.Function,
                    module: ir.Module, result_type: ir.LiteralStructType,
                    fd: ir.Value, data: ir.Value, length: ir.Value) -> None:
    """Write every byte, looping past a short write, and return the count or the error.

    A short write is not a failure and not the caller's problem here: `write(2)` may take
    fewer bytes than it was offered on a pipe, and a caller that had to loop for itself
    would write that loop at every call site. `sock_send` leaves the loop to the caller
    because a socket's partial write is information -- how much the peer's window took --
    and a file's is not.
    """
    _i8, _i8_ptr, i32, i64 = get_basic_types()

    done_slot = entry_alloca(builder, i64, name="done")
    builder.store(ir.Constant(i64, 0), done_slot)

    cond_bb = func.append_basic_block(name="write_cond")
    body_bb = func.append_basic_block(name="write_body")
    failure_bb = func.append_basic_block(name="write_failed")
    success_bb = func.append_basic_block(name="write_done")
    builder.branch(cond_bb)

    builder.position_at_end(cond_bb)
    done = builder.load(done_slot, name="written_so_far")
    builder.cbranch(builder.icmp_signed("<", done, length, name="more_to_write"),
                    body_bb, success_bb)

    builder.position_at_end(body_bb)
    rest = builder.gep(data, [done], name="rest")
    remaining = builder.sub(length, done, name="remaining")
    wrote = builder.call(write_fn, [fd, rest, remaining], name="wrote")
    # A zero-byte write would spin forever, so it ends the loop the same way an error
    # does: nothing moved, and the descriptor is not going to take the rest either.
    progressed = builder.icmp_signed(">", wrote, ir.Constant(i64, 0), name="progressed")
    advance_bb = func.append_basic_block(name="write_advance")
    interrupted_bb = func.append_basic_block(name="write_interrupted")
    builder.cbranch(progressed, advance_bb, interrupted_bb)

    # An interrupted write moved nothing, so the loop asks again from the same offset
    # (probe P13). A zero-byte answer is not interrupted and still ends the loop.
    builder.position_at_end(interrupted_bb)
    was_error = builder.icmp_signed("<", wrote, ir.Constant(i64, 0), name="write_failed")
    retry_bb = func.append_basic_block(name="write_retry_check")
    builder.cbranch(was_error, retry_bb, failure_bb)

    builder.position_at_end(retry_bb)
    builder.cbranch(emit_is_eintr(builder, module), cond_bb, failure_bb)

    builder.position_at_end(advance_bb)
    builder.store(builder.add(done, wrote, name="advanced"), done_slot)
    builder.branch(cond_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, length, 8))


def generate_fd_write_str(module: ir.Module) -> None:
    """Emit `Result<i64, FileError> sushi_io_files_fd_write_str(i32 fd, string s)`.

    The string's OWN bytes, with no `to_bytes()` copy in front of them: a fat pointer
    already carries a pointer and a byte count, which is exactly what `write(2)` wants.
    The count is the stored i64 `size` field.
    """
    _i8, _i8_ptr, i32, i64 = get_basic_types()
    write_fn = get_platform_module('files').declare_write(module)

    string_ty = get_string_type()
    result_type = get_result_type(i64, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, string_ty]),
                       name="sushi_io_files_fd_write_str")
    fd, text = func.args
    fd.name, text.name = "fd", "s"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    buffer = builder.extract_value(text, 0, name="str_data")
    size = string_size_i64(builder, text, name="str_size")
    _emit_write_all(builder, func, write_fn, module, result_type, fd, buffer, size)
