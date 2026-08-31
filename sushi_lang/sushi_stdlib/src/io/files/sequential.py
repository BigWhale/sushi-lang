"""The SEQUENTIAL half of the descriptor layer: read, write, readln, seek, isatty.

`positional.py` is the twin, and the difference is the whole point of having both. A
`pread` takes its offset as an argument and never moves the descriptor's own position,
which is what makes it safe to share; the calls here move it, which is what makes them
the ones a `File` handle is written on (HANDLES.md, Phase 5).

Every one of these is what a builtin `file` method used to reach through libc stdio.
`fopen` and `fgets` buffer, and `File.write()` writing through `write(2)` beside a
buffered `printf` is what put the console out of order in ruling R12; there is one route
to a descriptor now, and this is the file half of it.
"""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types, get_byte_array_type, get_result_type, get_string_type,
    get_unit_enum_type,
)
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.io.files.errno import (
    emit_errno_err_result, emit_file_error_tag)
from sushi_lang.sushi_stdlib.src.io.files.results import emit_ok_result, emit_err_result
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_free, declare_malloc
from sushi_lang.sushi_stdlib.src.error_emission import emit_runtime_error

# `fd_readln`'s buffer step, and its `pread` size on the seekable path. It bounds the
# reallocations on both paths and the system calls on the chunked one.
_LINE_CHUNK = 128


def generate_ir(module: ir.Module) -> None:
    """Generate the sequential descriptor primitives."""
    generate_fd_read(module)
    generate_fd_write(module)
    generate_fd_write_str(module)
    generate_fd_readln(module)
    generate_fd_seek(module)
    generate_fd_isatty(module)


def generate_fd_read(module: ir.Module) -> None:
    """Emit `Result<u8[], FileError> sushi_io_files_fd_read(i32 fd, i32 max)`.

    ONE `read(2)`, so the answer carries what arrived and may be shorter than asked for.
    An EMPTY array is end of file and not an error, which is what lets a caller loop
    until the answer is empty. `cap` stays `max`, so the array destructor frees the whole
    buffer whatever `len` says -- the same rule `fd_pread` follows.
    """
    _i8, i8_ptr, i32, i64 = get_basic_types()
    read_fn = get_platform_module('files').declare_read(module)
    malloc_fn = declare_malloc(module)
    free_fn = declare_free(module)

    array_ty = get_byte_array_type()
    result_type = get_result_type(array_ty, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, i32]),
                       name="sushi_io_files_fd_read")
    fd, maximum = func.args
    fd.name, maximum.name = "fd", "max"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    max64 = builder.zext(maximum, i64, name="max64")
    buffer = builder.call(malloc_fn, [max64], name="read_buf")
    is_null = builder.icmp_unsigned("==", buffer, ir.Constant(i8_ptr, None),
                                    name="alloc_failed")

    alloc_fail_bb = func.append_basic_block(name="alloc_fail")
    do_read_bb = func.append_basic_block(name="do_read")
    builder.cbranch(is_null, alloc_fail_bb, do_read_bb)

    builder.position_at_end(alloc_fail_bb)
    emit_runtime_error(module, builder, "RE2021")

    builder.position_at_end(do_read_bb)
    got = builder.call(read_fn, [fd, buffer, max64], name="got")
    ok = builder.icmp_signed(">=", got, ir.Constant(i64, 0), name="read_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    # The tag before the free: free() overwrites errno.
    builder.position_at_end(failure_bb)
    tag = emit_file_error_tag(builder, module)
    builder.call(free_fn, [buffer])
    builder.ret(emit_err_result(builder, result_type, tag))

    builder.position_at_end(success_bb)
    descriptor = ir.Constant(array_ty, ir.Undefined)
    descriptor = builder.insert_value(descriptor,
                                      builder.trunc(got, i32, name="got32"), 0,
                                      name="desc_len")
    descriptor = builder.insert_value(descriptor, maximum, 1, name="desc_cap")
    descriptor = builder.insert_value(descriptor, buffer, 2, name="descriptor")
    builder.ret(emit_ok_result(builder, result_type, descriptor, 16))


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

    done_slot = builder.alloca(i64, name="done")
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
    builder.cbranch(progressed, advance_bb, failure_bb)

    builder.position_at_end(advance_bb)
    builder.store(builder.add(done, wrote, name="advanced"), done_slot)
    builder.branch(cond_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type,
                               builder.trunc(length, i32, name="wrote32"), 4))


def generate_fd_write(module: ir.Module) -> None:
    """Emit `Result<i32, FileError> sushi_io_files_fd_write(i32 fd, u8[] data)`.

    Every byte goes or it is an error; the Ok value is the count, which equals the
    array's length. The buffer belongs to the caller and is a borrow, so this never
    frees it.
    """
    _i8, _i8_ptr, i32, i64 = get_basic_types()
    write_fn = get_platform_module('files').declare_write(module)

    array_ty = get_byte_array_type()
    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, array_ty]),
                       name="sushi_io_files_fd_write")
    fd, data = func.args
    fd.name, data.name = "fd", "data"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    length = builder.extract_value(data, 0, name="data_len")
    buffer = builder.extract_value(data, 2, name="data_ptr")
    _emit_write_all(builder, func, write_fn, module, result_type, fd, buffer,
                    builder.zext(length, i64, name="data_len64"))


def generate_fd_write_str(module: ir.Module) -> None:
    """Emit `Result<i32, FileError> sushi_io_files_fd_write_str(i32 fd, string s)`.

    The string's OWN bytes, with no `to_bytes()` copy in front of them: a fat pointer
    already carries a pointer and a byte count, which is exactly what `write(2)` wants.
    The count is the i32 `size` field and is widened here -- never passed as a `mem*`
    length directly (`docs/design/string-representation.md`).
    """
    _i8, _i8_ptr, i32, i64 = get_basic_types()
    write_fn = get_platform_module('files').declare_write(module)

    string_ty = get_string_type()
    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, string_ty]),
                       name="sushi_io_files_fd_write_str")
    fd, text = func.args
    fd.name, text.name = "fd", "s"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    buffer = builder.extract_value(text, 0, name="str_data")
    size = builder.extract_value(text, 1, name="str_size")
    _emit_write_all(builder, func, write_fn, module, result_type, fd, buffer,
                    builder.zext(size, i64, name="str_size64"))


def generate_fd_readln(module: ir.Module) -> None:
    """Emit `Result<string, FileError> sushi_io_files_fd_readln(i32 fd)`.

    One line, the newline STRIPPED, and an empty string at end of file -- the contract
    the builtin `readln` had, which is what lets a caller loop until the answer is empty.

    **Two paths, chosen by whether the descriptor can seek.** Reading one byte at a time
    is the only shape that is correct on a descriptor that CANNOT seek: a pipe, a socket
    or a terminal cannot give back an over-read, so a chunked read there would swallow
    bytes the next reader owns -- and `stdin` is a pipe. It is also ten times slower,
    measured at 200 000 lines: 4.49s against 0.47s for the buffered `fgets` this
    replaces, with 3.3s of that in the kernel across 9.3 million system calls.

    So a SEEKABLE descriptor takes the chunked path instead, and it is written in
    ABSOLUTE positions throughout -- `pread` at a computed offset, then one `lseek` to
    just past the newline. Relative seek-back arithmetic would be the same idea and one
    sign error away from silently reading the wrong bytes.

    Phase 7's `BufReader` is still the general answer, and R13 sends `lines()` there.
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    read_fn = platform_files.declare_read(module)
    pread_fn = platform_files.declare_pread(module)
    lseek_fn = platform_files.declare_lseek(module)
    malloc_fn = declare_malloc(module)
    realloc_fn = _declare_realloc(module)
    free_fn = declare_free(module)

    string_ty = get_string_type()
    result_type = get_result_type(string_ty, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32]),
                       name="sushi_io_files_fd_readln")
    fd = func.args[0]
    fd.name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    cap_slot = builder.alloca(i64, name="cap")
    len_slot = builder.alloca(i64, name="len")
    buf_slot = builder.alloca(i8_ptr, name="buf")
    one_byte = builder.alloca(i8, name="one_byte")
    start_slot = builder.alloca(i64, name="line_start")

    builder.store(ir.Constant(i64, _LINE_CHUNK), cap_slot)
    builder.store(ir.Constant(i64, 0), len_slot)
    initial = builder.call(malloc_fn, [ir.Constant(i64, _LINE_CHUNK)], name="line_buf")
    builder.store(initial, buf_slot)

    alloc_ok_bb = func.append_basic_block(name="alloc_ok")
    alloc_fail_bb = func.append_basic_block(name="alloc_fail")
    builder.cbranch(builder.icmp_unsigned("==", initial, ir.Constant(i8_ptr, None),
                                          name="alloc_failed"),
                    alloc_fail_bb, alloc_ok_bb)

    builder.position_at_end(alloc_fail_bb)
    emit_runtime_error(module, builder, "RE2021")

    finish_bb = func.append_basic_block(name="line_finish")
    failure_bb = func.append_basic_block(name="line_failed")

    # Can this descriptor seek? A pipe, a socket and a terminal answer -1 with ESPIPE,
    # and each must not be over-read.
    builder.position_at_end(alloc_ok_bb)
    here = builder.call(lseek_fn, [fd, ir.Constant(i64, 0),
                                   ir.Constant(i32, platform_files.SEEK_CUR)],
                        name="here")
    builder.store(here, start_slot)
    chunked_bb = func.append_basic_block(name="line_chunked")
    byte_bb = func.append_basic_block(name="line_by_byte")
    builder.cbranch(builder.icmp_signed(">=", here, ir.Constant(i64, 0),
                                        name="can_seek"),
                    chunked_bb, byte_bb)

    _emit_readln_by_byte(builder, func, module, fd, read_fn, realloc_fn, one_byte,
                         buf_slot, len_slot, cap_slot, byte_bb, finish_bb, failure_bb,
                         alloc_fail_bb)
    _emit_readln_chunked(builder, func, module, fd, pread_fn, lseek_fn, realloc_fn,
                         platform_files, buf_slot, len_slot, cap_slot, start_slot,
                         chunked_bb, finish_bb, failure_bb, alloc_fail_bb)

    builder.position_at_end(failure_bb)
    tag = emit_file_error_tag(builder, module)
    builder.call(free_fn, [builder.load(buf_slot)])
    builder.ret(emit_err_result(builder, result_type, tag))

    # A NUL after the last byte, so the buffer is also a C string: nothing here promises
    # that, but every consumer that hands one to libc gets it for free.
    builder.position_at_end(finish_bb)
    final_len = builder.load(len_slot, name="final_len")
    final_buf = builder.load(buf_slot, name="final_buf")
    builder.store(ir.Constant(i8, 0),
                  builder.gep(final_buf, [final_len], name="terminator"))

    text = ir.Constant(string_ty, ir.Undefined)
    text = builder.insert_value(text, final_buf, 0, name="str_data")
    text = builder.insert_value(text, builder.trunc(final_len, i32, name="len32"), 1,
                                name="str_size")
    text = builder.insert_value(text, ir.Constant(i8, 1), 2, name="str_owned")
    builder.ret(emit_ok_result(builder, result_type, text, 16))


def _emit_grow(builder: ir.IRBuilder, func: ir.Function, realloc_fn: ir.Function,
               buf_slot: ir.Value, cap_slot: ir.Value, needed: ir.Value,
               name: str, alloc_fail_bb: ir.Block) -> ir.Block:
    """Grow the line buffer until it holds `needed` bytes. Answers the block to go on in.

    Both readln paths grow the same buffer on the same terms, and the terminator's byte
    is counted here so neither has to remember it.
    """
    _i8, i8_ptr, _i32, i64 = get_basic_types()

    cond_bb = func.append_basic_block(name=f"{name}_grow_cond")
    do_bb = func.append_basic_block(name=f"{name}_grow")
    done_bb = func.append_basic_block(name=f"{name}_grown")
    builder.branch(cond_bb)

    builder.position_at_end(cond_bb)
    room = builder.add(needed, ir.Constant(i64, 1), name="with_terminator")
    builder.cbranch(builder.icmp_signed(">", room, builder.load(cap_slot),
                                        name="needs_room"),
                    do_bb, done_bb)

    builder.position_at_end(do_bb)
    bigger = builder.add(builder.load(cap_slot), ir.Constant(i64, _LINE_CHUNK),
                         name="bigger")
    grown = builder.call(realloc_fn, [builder.load(buf_slot), bigger], name="grown")
    ok_bb = func.append_basic_block(name=f"{name}_grow_ok")
    builder.cbranch(builder.icmp_unsigned("==", grown, ir.Constant(i8_ptr, None),
                                          name="grow_failed"),
                    alloc_fail_bb, ok_bb)

    builder.position_at_end(ok_bb)
    builder.store(grown, buf_slot)
    builder.store(bigger, cap_slot)
    builder.branch(cond_bb)

    builder.position_at_end(done_bb)
    return done_bb


def _emit_readln_by_byte(builder, func, module, fd, read_fn, realloc_fn, one_byte,
                         buf_slot, len_slot, cap_slot, entry_bb, finish_bb, failure_bb,
                         alloc_fail_bb) -> None:
    """One `read(2)` per byte: the only shape a descriptor that cannot seek allows."""
    i8, _i8_ptr, _i32, i64 = get_basic_types()

    builder.position_at_end(entry_bb)
    cond_bb = func.append_basic_block(name="byte_cond")
    builder.branch(cond_bb)

    # `got == 0` is end of file and `got < 0` is a real failure; confusing the two would
    # make a truncated read look like a clean end.
    builder.position_at_end(cond_bb)
    got = builder.call(read_fn, [fd, one_byte, ir.Constant(i64, 1)], name="got")
    eof_bb = func.append_basic_block(name="byte_eof_check")
    have_bb = func.append_basic_block(name="byte_have")
    builder.cbranch(builder.icmp_signed("<", got, ir.Constant(i64, 0), name="read_failed"),
                    failure_bb, eof_bb)

    builder.position_at_end(eof_bb)
    builder.cbranch(builder.icmp_signed("==", got, ir.Constant(i64, 0), name="eof"),
                    finish_bb, have_bb)

    builder.position_at_end(have_bb)
    char = builder.load(one_byte, name="char")
    # The newline ends the line and is DROPPED, so a caller never strips it and a line's
    # text is the same whether or not the file ends with one.
    store_bb = func.append_basic_block(name="byte_store")
    builder.cbranch(builder.icmp_signed("==", char, ir.Constant(i8, ord("\n")),
                                        name="is_newline"),
                    finish_bb, store_bb)

    builder.position_at_end(store_bb)
    length = builder.load(len_slot, name="line_len")
    _emit_grow(builder, func, realloc_fn, buf_slot, cap_slot,
               builder.add(length, ir.Constant(i64, 1), name="need"), "byte",
               alloc_fail_bb)
    builder.store(char, builder.gep(builder.load(buf_slot), [builder.load(len_slot)],
                                    name="at"))
    builder.store(builder.add(builder.load(len_slot), ir.Constant(i64, 1)), len_slot)
    builder.branch(cond_bb)


def _emit_readln_chunked(builder, func, module, fd, pread_fn, lseek_fn, realloc_fn,
                         platform_files, buf_slot, len_slot, cap_slot, start_slot,
                         entry_bb, finish_bb, failure_bb, alloc_fail_bb) -> None:
    """`pread` a chunk at a computed offset, then one `lseek` past the newline.

    Every position here is ABSOLUTE: the line's start is read once, each `pread` asks for
    `start + len`, and the final `lseek` is a SEEK_SET to `start + consumed`. A relative
    seek-back would be the same idea with a sign error waiting in it, and the failure
    mode -- reading the wrong bytes, silently -- is the worst kind.
    """
    i8, _i8_ptr, i32, i64 = get_basic_types()
    chunk = ir.Constant(i64, _LINE_CHUNK)

    builder.position_at_end(entry_bb)
    cond_bb = func.append_basic_block(name="chunk_cond")
    builder.branch(cond_bb)

    builder.position_at_end(cond_bb)
    length = builder.load(len_slot, name="have")
    _emit_grow(builder, func, realloc_fn, buf_slot, cap_slot,
               builder.add(length, chunk, name="need"), "chunk", alloc_fail_bb)

    length = builder.load(len_slot, name="have_now")
    start = builder.load(start_slot, name="line_start_at")
    into = builder.gep(builder.load(buf_slot), [length], name="into")
    got = builder.call(pread_fn, [fd, into, chunk,
                                  builder.add(start, length, name="read_at")],
                       name="got")

    eof_bb = func.append_basic_block(name="chunk_eof_check")
    scan_bb = func.append_basic_block(name="chunk_scan")
    builder.cbranch(builder.icmp_signed("<", got, ir.Constant(i64, 0), name="read_failed"),
                    failure_bb, eof_bb)

    # End of file: the position moves to just past what was consumed, so the NEXT readln
    # answers empty rather than re-reading the tail.
    builder.position_at_end(eof_bb)
    at_eof_bb = func.append_basic_block(name="chunk_at_eof")
    builder.cbranch(builder.icmp_signed("==", got, ir.Constant(i64, 0), name="eof"),
                    at_eof_bb, scan_bb)

    builder.position_at_end(at_eof_bb)
    builder.call(lseek_fn, [fd, builder.add(start, builder.load(len_slot), name="eof_at"),
                            ir.Constant(i32, platform_files.SEEK_SET)])
    builder.branch(finish_bb)

    # Scan only the bytes this pread delivered, from `len` to `len + got`.
    builder.position_at_end(scan_bb)
    scan_slot = builder.alloca(i64, name="scan")
    builder.store(length, scan_slot)
    limit = builder.add(length, got, name="limit")
    scan_cond_bb = func.append_basic_block(name="chunk_scan_cond")
    scan_body_bb = func.append_basic_block(name="chunk_scan_body")
    scan_next_bb = func.append_basic_block(name="chunk_scan_next")
    found_bb = func.append_basic_block(name="chunk_found")
    exhausted_bb = func.append_basic_block(name="chunk_exhausted")
    builder.branch(scan_cond_bb)

    builder.position_at_end(scan_cond_bb)
    at = builder.load(scan_slot, name="at")
    builder.cbranch(builder.icmp_signed("<", at, limit, name="more"),
                    scan_body_bb, exhausted_bb)

    builder.position_at_end(scan_body_bb)
    byte = builder.load(builder.gep(builder.load(buf_slot), [at], name="byte_at"),
                        name="byte")
    builder.cbranch(builder.icmp_signed("==", byte, ir.Constant(i8, ord("\n")),
                                        name="is_newline"),
                    found_bb, scan_next_bb)

    builder.position_at_end(scan_next_bb)
    builder.store(builder.add(at, ir.Constant(i64, 1)), scan_slot)
    builder.branch(scan_cond_bb)

    # The line is everything before the newline; the newline itself is consumed and
    # dropped, so `consumed` is one past it.
    builder.position_at_end(found_bb)
    newline_at = builder.load(scan_slot, name="newline_at")
    builder.store(newline_at, len_slot)
    builder.call(lseek_fn, [fd, builder.add(start,
                                            builder.add(newline_at, ir.Constant(i64, 1),
                                                        name="consumed"),
                                            name="resume_at"),
                            ir.Constant(i32, platform_files.SEEK_SET)])
    builder.branch(finish_bb)

    builder.position_at_end(exhausted_bb)
    builder.store(limit, len_slot)
    builder.branch(cond_bb)


def generate_fd_seek(module: ir.Module) -> None:
    """Emit `Result<i64, FileError> sushi_io_files_fd_seek(i32 fd, i64 off, i32 whence)`.

    The Ok value is the NEW position, which is what makes `tell` need no generator of its
    own: it is `fd_seek(fd, 0, SEEK_CUR)`. The whence is an INTENT -- 0 the start, 1 the
    current position, 2 the end -- mapped here to the platform's own `SEEK_*`, exactly as
    `fd_open` maps its intent to the `O_*` flags. POSIX happens to fix these three at 0,
    1 and 2, so the mapping is an identity today; it is written out anyway, because a
    Sushi module spelling a platform number is the mistake the intent exists to prevent.
    """
    _i8, _i8_ptr, i32, i64 = get_basic_types()
    platform_files = get_platform_module('files')
    lseek_fn = platform_files.declare_lseek(module)

    result_type = get_result_type(i64, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, i64, i32]),
                       name="sushi_io_files_fd_seek")
    fd, offset, whence = func.args
    fd.name, offset.name, whence.name = "fd", "offset", "whence"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    whence_slot = builder.alloca(i32, name="whence_slot")
    builder.store(ir.Constant(i32, platform_files.SEEK_SET), whence_slot)

    # An unrecognised whence seeks from the START, which is the reading of a number this
    # function does not know that can lose no data.
    done_bb = func.append_basic_block(name="whence_done")
    switch = builder.switch(whence, done_bb)
    for intent, mapped in ((1, platform_files.SEEK_CUR), (2, platform_files.SEEK_END)):
        block = func.append_basic_block(name=f"whence_{intent}")
        switch.add_case(ir.Constant(i32, intent), block)
        builder.position_at_end(block)
        builder.store(ir.Constant(i32, mapped), whence_slot)
        builder.branch(done_bb)

    builder.position_at_end(done_bb)
    where = builder.call(lseek_fn, [fd, offset, builder.load(whence_slot)],
                         name="position")
    ok = builder.icmp_signed(">=", where, ir.Constant(i64, 0), name="seek_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, where, 8))


def generate_fd_isatty(module: ir.Module) -> None:
    """Emit `i8 sushi_io_files_fd_isatty(i32 fd)`, a BARE bool and not a Result.

    Asking cannot fail in a way a caller can act on: a descriptor that is not a terminal
    and a descriptor that is not open both answer 0. Wrapping that in a Result would put
    an error arm in every call site for a failure that never arrives.
    """
    i8, _i8_ptr, i32, _i64 = get_basic_types()
    isatty_fn = get_platform_module('files').declare_isatty(module)

    func = ir.Function(module, ir.FunctionType(i8, [i32]),
                       name="sushi_io_files_fd_isatty")
    fd = func.args[0]
    fd.name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    answer = builder.call(isatty_fn, [fd], name="isatty_rc")
    builder.ret(builder.zext(
        builder.icmp_signed("!=", answer, ir.Constant(i32, 0), name="is_terminal"),
        i8, name="as_bool"))


def _declare_realloc(module: ir.Module) -> ir.Function:
    """Declare libc realloc(). `fd_readln` is the only generator here that grows a buffer."""
    _i8, i8_ptr, _i32, i64 = get_basic_types()
    try:
        return module.get_global("realloc")
    except KeyError:
        return ir.Function(module, ir.FunctionType(i8_ptr, [i8_ptr, i64]), name="realloc")
