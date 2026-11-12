# Standard library: modules, homes and the rules for a change

This file holds the module list, the home module of each predefined enum, the re-export
chains, the I/O layering, the constructor vocabulary, and the rules for a change to the
stdlib or the toolchain tools. Read this file when you write a program that imports the
stdlib, or when you change `sushi_lang/sushi_stdlib/**` or `toolchain/`. The API of each
module is in `docs/stdlib/**`. The import tiers of methods (R1-R10) are in
`docs/agents/language-rules.md` (Extension visibility).

## Modules

Import syntax: `use <io/fs>`, `use <time>`. Available modules: `<time>`, `<math>`,
`<random>`, `<sys/env>`, `<sys/process>`, `<io/buf>`, `<io/contracts>`, `<io/error>`,
`<io/files>`, `<io/fs>`, `<io/path>`, `<collections/strings>`, `<collections/hashmap>`, `<collections/iter>`,
`<compression/zlib>`, `<encoding/base64>`, `<encoding/binary>`, `<encoding/hex>`, `<encoding/msgpack>`, `<net/tcp>`, `<net/udp>`, `<net/dns>`,
`<net/ip>`, `<net/url>`, `<net/error>`, `<net/handle>`, `<text/ascii>`, `<toolchain/slib>`. API details: `docs/stdlib/**`.
An import brings a module's NAMES; a method of a built-in type needs NO import (`s.len()`,
`s.lines()`, `xs.map(f)`, `b.is_ascii_digit()`, `.sort()`), see Extension visibility in
`docs/agents/language-rules.md`.

## Predefined enums and re-exports

**A predefined enum has a HOME, and the import gates the bare name (#574, Ruling 3)**:
`FileMode` -> `<io/fs>`; `IoError`, `FileError` -> `<io/error>`; `SeekFrom` ->
`<io/contracts>`; `NetError` -> `<net/error>`; `ProcessError` -> `<sys/process>`;
`EnvError` -> `<sys/env>`; `MathError` -> `<math>`. `StdError` stays global. The alias
holds it (`fs.FileMode.Read()`); the bare name with no import is an unknown-type error with the import
in the help, the `HashMap` rule. The nine stay synthesized in `passes/collect/enums.py`,
each stamped (`EnumType.home_module`), and the `namespaces` pass reads the stamp. **The
home is reached through a `public use` (#586)**: `<io/contracts>` re-exports `<io/error>`;
`<io/fs>` and `<io/buf>` re-export `<io/contracts>`; `<net/tcp>` re-exports `<net/error>`,
`<net/handle>`, `<net/ip>` and `<io/contracts>`; `<net/udp>` re-exports `<net/error>`,
`<net/handle>` and `<net/ip>` (every address that leaves the net modules is an `IpAddr`:
`peer_ip()` answers `IpAddr | NetError`, `Datagram.peer()` parses the field); `<net/dns>` and `<net/ip>` re-export `<net/error>`;
the registry module `<io/files>` re-exports `<io/error>` (`REEXPORTS` in
`files_funcs.py`). So `use <io/fs>` alone writes `| IoError`, `IoError.NotFound`,
`SeekFrom.Start` and `FileError.NotFound`, `fs.IoError` holds behind the alias, and
`use <net/tcp>` alone matches `IoError` from a read. `<sys/env>`, `<sys/process>` and
`<math>` stay their own homes. The conversions `FileError as IoError` and `NetError as
IoError` live in `io/error.sushi`, which imports `<net/error>` with a plain `use`: `use <io/fs>`
loads the `<net/error>` unit and does not bring the name `NetError`.
`close_socket` (the one guarded `sock_close`) lives in `<net/handle>`, not in `<net/error>`.

## I/O layering

**`<io/fs>` is the handle module**: `File`, `open()`, every File method, and the three
console handles `stdin`/`stdout`/`stderr` as `File` CONSTANTS over descriptors 0, 1 and
2. `<io/files>` is the layer under it -- path utilities and the `fd_*` primitives.
There is no `<io/stdio>` module, and `file`, `stdin`, `stdout` and `stderr` are not
keywords. **Positional and shared I/O**: `read_at(offset, count)` and
`write_at(offset, data)` take the offset as an ARGUMENT and move nothing, so they are the
answer for concurrent reads of one file. `share()` on a `File` and on a `TcpListener` is
`dup(2)`: a plain-borrow receiver, and the answer is a second OWNER over a SHARED open
file description, so the offset is shared and each handle closes on its own drop. It is
concrete on each handle because a perk has no `Self` (HANDLES.md R7); `TcpListener` gets it
and no contract (R9). The socket primitive is `sock_dup`, beside `fd_dup`.

**`<io/buf>` is the buffered layer, and it owns LINE ITERATION.** `BufReader@(R)` over
any `Reader`, `BufWriter@(W)` over any `Writer`, one system call per WINDOW. Both
constructors are statics named `new` (`BufReader.new(nom f, 8192)`,
`BufWriter.new(nom f, 8192)`) that TAKE the handle, and `R`/`W` come from the handle
argument in every position (#573), so `BufReader.new(nom f, 4096).lines()` needs no
annotation. Neither can fail, so neither carries a channel (no `??` at the call); the structs are declared `BufReader@(R: Reader)` and `BufWriter@(W: Writer)`.
`finish()` is `BufWriter`'s checked last drain and CONSUMES, a dropped one flushes and
loses the error. `r.lines()` takes the reader and answers `Lines@(R)`, whose `next()`
answers `Maybe@(Result@(string, IoError))` -- so `foreach(line?? in r.lines())` is the
line loop, and a `File` has none. Both buffered types implement the contract they wrap:
the contracts take `poke self` (ruling on #546), so a generic written over `Reader` takes
a `BufReader@(File)` and a `File` alike.

**`<io/contracts>` is what a handle can DO**: three perks and nothing else -- `Reader`
(`read`, `read_bytes`), `Writer` (`write`, `write_bytes`, `flush`) and `Seek` (`seek`).
`File` implements all three, `TcpStream` implements `Reader` and `Writer`, `TcpListener`
none. **Every contract method answers `IoError`**, and it must: a contract carries ONE
signature and there is no `Self`, so a method cannot answer `FileError` here and `NetError`
there. **A name has exactly one home** -- a perk method beside a concrete method of the
same name on one type is an error -- so `read`, `write`, `flush` and `seek` are the
contract's and not `File`'s. The line for the channel: a read, a write, a seek, `open()`
and `close()` answer `IoError`; construction, addressing and options keep the domain enum
(`connect`, `accept`, `peer_ip`, `set_timeouts`), and so do the path utilities
(`stat`, `walk`, `read_dir`) and the `fd_*` primitives. The conversions
`FileError as IoError` and `NetError as IoError` (`<io/error>`) turn those into `IoError`: the
stdlib calls them with `??`, and a user body that answers `IoError` may `??` either too. A contract WRITE
answers `~` and never a count: the primitives loop past a short write, and a discarded
value Result gets a warning at every bare-statement call site.

## Two results

**A stdlib function with two results answers a TUPLE** (PR #1142): `divmod` (`<math>`,
integers, `(T, T) | MathError`), `split_once` / `rsplit_once` (`Maybe@((string, string))`),
`enumerate` / `zip` / `partition` / `unzip` (`<collections/iter>`), HashMap `.pairs()`
(`Iterator@((K, V))`, beside `.entries()`), `<io/path>` `split` / `split_extension`,
`TcpStream.peer()`, `BufReader.into_parts()`, `decode_prefix`, `inflate_raw_prefix`. Two
BREAKING changes: `TcpListener.accept()` answers `(TcpStream, IpAddr) | NetError`, and
`MsgValue.Map` holds `(MsgValue, MsgValue)[]`. A generator row may name a tuple inside a
`Result`/`Maybe` (`get_tuple_type`, `type_definitions.py`, is the one layout; an unmappable
row type is CE0145); a BARE tuple answered by a generator is not supported -- a
`src_sushi/` function returns one with no restriction.

## Rules for a change to the stdlib
- **The constructor vocabulary (#571)**: a function that builds a value of a type from its
  arguments and allocates for it is a STATIC named `new` (`List.new()`, `BufReader.new(nom
  f, 8192)`); a function that DOES something -- opens, connects, decodes, reads a header --
  is a FREE FUNCTION named by its verb, with NO unit prefix (`connect`, not `tcp_connect`;
  the module is the namespace, `use <m> as m` tells two verbs apart, and a bare
  clash is an error). A verb's OBJECT stays (`parse_ip`, `parse_url`), and so do the `fd_*`/`sock_*`
  LAYER and `zlib_*` FORMAT prefixes.
- Editing `src/**`: `./sushic --build-stdlib` rebuilds at once; it is not needed for
  correctness (the compiler-source digest covers the generators and `ensure_stdlib_built`
  rebuilds a stale stdlib). A `src_sushi` module is in the cache key through the dependency graph.
- A stdlib symbol that wants a C string takes `i8*` and the CALL SITE marshals it through
  `emit_cstr_arg` (the one seam — see the table above). Do not marshal inside a generated body.
- **`<collections/strings>` has TWO halves**: the built-in methods are the `.bc` unit, and
  `src_sushi/collections/strings.sushi` (a `SOURCE_STDLIB_MODULES` row, NOT in `_virtual_units`)
  holds the methods written in Sushi (`parse_*`, `to_hex`/`to_bin`/`to_fixed`, `lines`,
  `split_whitespace`, `chars`, the `trim_*_matches` pair, `StringBuilder`). A new string method
  goes in the Sushi half; `TWO_HALF_MODULES` in `tests/unit/test_stdlib_build_is_one_table.py`
  names it. `to_fixed` calls libc `snprintf` through a private `unsafe external` block.
- Sushi-source modules go in `src_sushi/` + one `SOURCE_STDLIB_MODULES` entry + one
  `_virtual_units` entry; they are injected as ordinary units and need `public fn` on
  concrete exports and `public extend` on API methods. A module that declares methods on a
  built-in type is a row of `BUILTIN_TYPE_METHOD_MODULES` (`stdlib_registry.py`), so the build
  loads it when the program names one of its methods.
- **Toolchain tools** (Sushi programs that compile Sushi) live in top-level `toolchain/`
  (repo-only, never shipped): sources in `toolchain/src/`, built into `toolchain/bin/`
  (gitignored) by `./toolchain/build.py`. `sushic` delegates to a built tool when it
  exists (`--lib-info` → `slib-info`) and falls back to Python otherwise;
  `SUSHI_TOOLCHAIN=off` skips the tools, `SUSHI_TOOLCHAIN_BIN` overrides the directory.
  Contract: `toolchain/README.md`.

