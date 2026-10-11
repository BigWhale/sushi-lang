# Files and I/O

Console, files, buffered reading and writing, paths, and the contracts that describe what a handle can do.

## Chapters

- [Console](console.md) - `println`, `print`, `stdin`, `stdout`, `stderr`
- [Files](files.md) - File operations with error handling
- [Buffered](buf.md) - `BufReader` and `BufWriter`: one system call per window
- [Contracts](contracts.md) - `Reader`, `Writer` and `Seek`: what a handle can do
- [IO errors](error.md) - `IoError` and `FileError`
- [Path algebra](path.md) - Lexical path manipulation
- [File-system ops](fs.md) - `stat`, recursive walk, `mkdir_all`, `remove_all`

Back to the [Standard library](../../standard-library.md).
