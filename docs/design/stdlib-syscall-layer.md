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
