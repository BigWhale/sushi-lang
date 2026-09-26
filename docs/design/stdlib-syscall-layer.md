# The stdlib system-call layer

**Status: DESCRIPTIVE.** This page records how the stdlib generators under
`sushi_lang/sushi_stdlib/src/` reach the operating system, and why. The code is the
authority on WHAT is emitted; this page is the authority on WHY.

## One route to a descriptor

Every `File` method goes to a descriptor through `read(2)`, `write(2)`, `lseek(2)` and
their positional twins. Nothing goes through libc stdio. `fopen` and `fgets` buffer, and a
`File.write()` through `write(2)` beside a buffered `printf` put the console output out of
order (ruling R12). There is one route to a descriptor now.

The descriptor layer of `<io/files>` has two halves:

- `io/files/sequential.py`: `read`, `write`, `readln`, `seek`. These calls MOVE the
  position of the descriptor, so a `File` handle is written on them.
- `io/files/positional.py`: `pread`, `pwrite`. These calls take the offset as an argument
  and do not move the position, so they are safe on a shared descriptor.

## The line reader (`fd_readln`)

`sushi_io_files_fd_readln(i32 fd)` answers `Result@(Maybe@(string), FileError)`: one line,
with the newline removed. A blank line and the end of file are different answers: a blank
line is `Some("")` and the end is `None`. The old contract answered an empty string for
both, so a file with a blank line in it stopped there, and a caller could not tell a short
file from a failed read. The end of file after some bytes is a last line with no newline
after it, and that is a line.

### Two paths, chosen by whether the descriptor can seek

The reader first asks `lseek(fd, 0, SEEK_CUR)`. A pipe, a socket and a terminal answer -1
(`ESPIPE`).

- **By byte** (the descriptor cannot seek). One `read(2)` per byte. This is the only
  correct shape on such a descriptor: it cannot give back an over-read, so a chunked read
  would take bytes that the next reader owns. `stdin` is frequently a pipe.
- **Chunked** (the descriptor can seek). `pread` reads a chunk of 128 bytes at a computed
  offset, the reader finds the newline in the bytes that arrived, and one `lseek` moves the
  position to the byte after the newline.

The by-byte path is slow. Measured on 2026-08-31 at 200 000 lines: 4.49s, against 0.47s
for the buffered `fgets` it replaced, with 3.3s of that in the kernel across 9.3 million
system calls. That is why a seekable descriptor takes the chunked path.

### The chunked path uses ABSOLUTE positions

The start of the line is read one time. Each `pread` asks for `start + len`, and the final
`lseek` is a `SEEK_SET` to `start + consumed`. A relative seek back is the same idea, but
one sign error in it reads the wrong bytes with no error, and that is the worst failure.
At the end of file the position moves to just after what was consumed, so the next
`readln` answers `None` and does not read the tail again.

### The shared state

Both paths write one buffer, one length, one capacity and one end-of-file flag, and both
leave through the same three blocks (the finish, the failure, the allocation failure). The
generator keeps this state in one frozen dataclass, `_ReadlnFrame`, and each path takes the
frame plus what is its own: its entry block, and the functions only it calls. The buffer
grows by 128 bytes and always keeps one byte for the NUL terminator, so the line is also a
C string.

`fd_readln` is under `File.readln()`, the UNBUFFERED read. To read a whole file line by
line, use `BufReader@(R)` and its `lines()` in `<io/buf>` (`docs/stdlib/io/buf.md`).

## errno to an error tag

A failed libc call answers -1 (or a null pointer) and puts the cause in `errno`. The
generators turn that cause into the tag of a unit-variant error enum -- `FileError` for
`<io/files>`, `NetError` for `<net/socket>` -- and build a `Result.Err` with it.

- **One emitter.** `emit_errno_tag(builder, module, table, default)` in
  `sushi_lang/sushi_stdlib/src/errno_tags.py` reads `errno` and maps it through `table`,
  and an unmapped value maps to `default`. `emit_errno_err_result` adds the `Result.Err`.
  The module is under `src/` and not under `io/` or `net/`, because neither of those may
  import the other.
- **The tables.** `errno_to_file_error_table` and `errno_to_net_error_table` in
  `sushi_lang/backend/runtime/constants.py` are the only tables, with their defaults
  `ERRNO_DEFAULT_FILE_ERROR` and `ERRNO_DEFAULT_NET_ERROR`. `io/files/errno.py` and
  `net/errno.py` each give the emitter their table and their default, and nothing more.
- **The layout.** The `Result` bytes are built in `src/results.py` and nowhere else, and
  `errno` is read through `declare_errno_location` in `src/libc_declarations.py`, so the
  two error families read one `errno` through one declaration.
- **`NetError.ResolveFailed`** is the one `NetError` tag that no `errno` reaches. A
  `getaddrinfo` failure answers its own code, and only `EAI_SYSTEM` means "read `errno`";
  every other code is `ResolveFailed`.

### The order of the calls

`close()`, `free()` and `freeaddrinfo()` can all overwrite `errno`. This is the largest
single cause of a wrong variant. So every failure edge reads the tag FIRST, directly after
the failed call, and keeps it; then it cleans up; then it builds the `Err`.

### EINTR

A `read` or a `write` that a signal interrupts before a byte moves answers -1 with `EINTR`,
and the correct response is to ask again. `emit_is_eintr` (`io/files/errno.py`) is the
test, and the read and write loops go back to the call when it is true.
