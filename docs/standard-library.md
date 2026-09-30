# Sushi Standard Library

Complete reference for Sushi's standard library modules and types.

## Table of Contents

### Core Types
- [Result@(T, E)](stdlib/result.md) - Error handling for fallible operations
- [Maybe@(T)](stdlib/maybe.md) - Optional values
- `Own@(T)` - One value on the heap, with one owner (`Own.alloc`, `get`, `destroy`,
  `clone`). No import is necessary. See [Memory Management](memory-management.md).

### Collections
- [List@(T)](stdlib/collections/list.md) - Dynamic growable array
- [HashMap@(K, V)](stdlib/collections/hashmap.md) - Hash table with open addressing
- [Arrays](stdlib/collections/arrays.md) - Fixed and dynamic array methods
- [Strings](stdlib/collections/strings.md) - String manipulation methods
- [Iter combinators](stdlib/collections/iter.md) - `map`/`filter`/`fold` as methods on `List@(T)` and `T[]`, the free functions, and `compose`

### Encoding and Compression
- [Compression (zlib)](stdlib/compression/zlib.md) - DEFLATE and the zlib container (RFC 1950/1951)
- [MessagePack](stdlib/encoding/msgpack.md) - MessagePack decoder
- [Slib reader](stdlib/toolchain/slib.md) - `.slib` header and metadata reader

### I/O Operations
- [Console I/O](stdlib/io/console.md) - println, print, stdin/stdout/stderr
- [File I/O](stdlib/io/files.md) - File operations with error handling
- [Path algebra](stdlib/io/path.md) - Lexical path manipulation (join, basename, dirname, extension, normalize)
- [File-system ops](stdlib/io/fs.md) - stat, recursive walk, mkdir_all, remove_all
- [I/O contracts](stdlib/io/contracts.md) - `Reader`, `Writer`, `Seek`: what a handle can do
- [I/O errors](stdlib/io/error.md) - `IoError` and `FileError`, the error vocabulary of the io modules
- [Buffered I/O](stdlib/io/buf.md) - `BufReader`, `BufWriter`: one system call per window

### Networking

- [Socket primitives](stdlib/net/socket.md) - the raw BSD calls
- [Net errors](stdlib/net/error.md) - `NetError`, the error vocabulary of the net modules
- [Socket handles](stdlib/net/handle.md) - `close_socket`, the one release of a socket descriptor
- [TCP](stdlib/net/tcp.md) - `TcpStream` and `TcpListener`
- [UDP](stdlib/net/udp.md) - `UdpSocket`, send_to and recv_from
- [DNS](stdlib/net/dns.md) - a host name resolved into typed addresses
- [IP addresses](stdlib/net/ip.md) - `IpAddr`, parse and format, both families
- [URLs](stdlib/net/url.md) - lexical URL splitting

### System Modules
- [Math](stdlib/math.md) - Mathematical operations (abs, min, max, sqrt, pow, trig)
- [Random](stdlib/random.md) - Pseudo-random number generation (rand, rand_range, rand_f64, srand)
- [Time](stdlib/time.md) - Sleep functions, the wall clock and the monotonic clock
- [Environment](stdlib/env.md) - Environment variables and system information
- [Process Control](stdlib/process.md) - Process management (getcwd, chdir, exit, getpid, getuid)
- [Platform](stdlib/platform.md) - Platform detection and OS-specific utilities

## Quick Reference

### Importing Modules

```sushi
use <collections/strings>  # String methods
use <collections/hashmap>  # HashMap@(K, V)
use <collections/iter>     # Higher-order combinators (map/filter/fold/compose)
use <compression/zlib>     # DEFLATE and the zlib container
use <encoding/msgpack>     # MessagePack decoder
use <io/buf>               # BufReader, BufWriter: buffered over any handle
use <io/contracts>         # Reader, Writer, Seek (re-exports <io/error>)
use <io/error>             # IoError, FileError -- brought by <io/fs>, <io/buf>, <io/files>
use <io/files>             # the path utilities and the fd_* primitives
use <io/path>              # Lexical path manipulation
use <io/fs>                # File, open, stdin/stdout/stderr, stat, walk, mkdir_all
use <net/socket>           # the raw socket calls
use <net/error>            # NetError -- brought by <net/tcp>, <net/udp>, <net/dns>, <net/ip>
use <net/tcp>              # TcpStream, TcpListener
use <net/udp>              # UdpSocket
use <net/dns>              # resolve a host name
use <net/ip>               # IpAddr, parse and format
use <net/url>              # split a URL
use <toolchain/slib>       # read the header and metadata of a .slib
use <math>                 # Math functions
use <random>               # Random number generation
use <time>                 # Sleep and clock functions
use <sys/env>              # Environment variables
use <sys/process>          # Process control
```

### Common Patterns

#### Error Handling

```sushi
use <io/fs>

# Using ?? operator for propagation. The channel is the one open() answers,
# so a read propagates through it with no conversion in the middle.
fn read_config() string | IoError:
    let File f = open("config.txt", FileMode.Read())??
    return Result.Ok(f.read_all()??)

# Using pattern matching
match parse_number("42"):
    Result.Ok(n) -> println("Got: {n}")
    Result.Err(_) -> println("Parse failed")

# Using .realise() for defaults
let i32 port = parse_number(text).realise(8080)
```

#### Optional Values

```sushi
use <collections/strings>

# Safe array access
match arr.get(0):
    Maybe.Some(first) -> println("First: {first}")
    Maybe.None() -> println("Array empty")

# String searching
let string text = "hello world"
let Maybe@(i32) pos = text.find("world")
```

#### Collections

```sushi
use <collections/hashmap>

# List@(T) - no import required
let List@(i32) numbers = List.new()
numbers.push(1)
numbers.push(2)
numbers.push(3)

# HashMap@(K, V) - requires import
let HashMap@(string, i32) ages = HashMap.new()
ages.insert("Alice", 30)
match ages.get("Alice"):
    Maybe.Some(age) -> println("Age: {age}")
    Maybe.None() -> println("Not found")

# Arrays - built-in
let i32[] arr = from([1, 2, 3])
arr.push(4)
foreach(n in arr.iter()):
    println(n)
```

#### String Processing

```sushi
use <collections/strings>

let string text = "  Hello World  "
let string clean = text.trim().lower()  # "hello world"

let string[] parts = "a,b,c".split(',')
let string joined = ','.join(parts)  # "a,b,c"

let string path = "/home/user/file.txt"
let string filename = path.strip_prefix("/home/user/")  # "file.txt"
```

#### File I/O

```sushi
use <io/fs>

# Reading files. A handle closes itself at the end of the arm, so nothing
# calls close(), and every read answers IoError. A read changes the handle,
# so the binding is `poke f`: a bare binding is a read-only view (CE2414).
match open("data.txt", FileMode.Read()):
    Result.Ok(poke f) ->
        match f.read_all():
            Result.Ok(content) -> println(content)
            Result.Err(_) -> println("Read failed")
    Result.Err(IoError.NotFound) ->
        println("File not found")
    Result.Err(_) ->
        println("Other error")

# Writing files
match open("output.txt", FileMode.Write()):
    Result.Ok(poke f) ->
        match f.writeln("Mostly Harmless"):
            Result.Ok(_) -> println("written")
            Result.Err(_) -> println("Failed to write")
    Result.Err(_) ->
        println("Failed to open")

```

A buffered writer is better when the loop is long: one system call per window, not one
per line. `BufWriter` comes from `<io/buf>`. Each call answers `IoError`, so `??` goes in
a function that has the `| IoError` channel, not in `main`: `main` is bare, and a `??`
there is CE0131:

```sushi
use <io/fs>
use <io/buf>

fn greet() ~ | IoError:
    let BufWriter@(File) out = BufWriter.new(nom stdout.share()??, 8192)
    out.write_line("Mostly Harmless")??
    out.finish()??
    return Result.Ok(~)
```

## Module Overview

### Collections

**List@(T)** - Generic dynamic array (built-in, no import required):
- Construction: `List.new()`, `List.with_capacity()`
- Access: `get()`, `len()`, `is_empty()`, `capacity()`
- Modification: `push()`, `pop()`, `insert()`, `remove()`, `clear()`
- Capacity: `reserve()`, `shrink_to_fit()`
- Iteration: `iter()` for foreach loops
- Copy and inspection: `clone()`, `debug()`
- Memory: `free()`, `destroy()`

**HashMap@(K, V)** - Generic hash table (`use <collections/hashmap>`):
- Construction: `HashMap.new()`
- Operations: `insert()`, `get()`, `remove()`, `contains_key()`
- Size: `len()`, `is_empty()`, `tombstone_count()`, `rehash()`
- Iteration: `keys()`, `values()`, `entries()`
- Automatic resizing at 0.75 load factor
- Copy and inspection: `clone()`, `debug()`
- Memory: `free()`, `destroy()`

**Arrays** - Built-in array support:
- Fixed arrays: `i32[10]`
- Dynamic arrays: `i32[]` with `from([...])`
- Literals: `from([1, 2, 3])`, a repeated element `[0; 8]`, a range element `[0..=4]`
- Reading: `len()`, `get()`, `first()`, `last()`, `contains()`, `index_of()`, `iter()`
- Growing and shrinking (dynamic only): `push()`, `pop()`, `clear()`, `truncate()`,
  `capacity()`, `extend()`, `extend_range()`
- In place: `fill()`, `reverse()`
- Copies: `clone()`, `s(start, end)`, `ss(start, count)`; the hash: `hash()`
- Byte arrays (`u8[]`): `to_string()`, `to_string_checked()`
- Memory (dynamic only): `free()`, `destroy()`
- Safe access with `get()` returns `Maybe@(T)`
- Unsafe direct indexing: `arr[i]`
- Indexed assignment: `arr[i] := v` (bounds-checked; the element it replaces is freed)

**Strings** - methods from `use <collections/strings>`:
- Inspection, slicing, transformation, padding, stripping
- Splitting/joining, case conversion, parsing
- UTF-8 aware where needed
- Each unit that calls a string method must import the module itself (CE3015).
  `is_empty()` and `clone()` need no import

**Iter combinators** - higher-order functions (`use <collections/iter>`):
- Methods on `List@(T)` and on `T[]`: `xs.map(f)`, `xs.filter(pred)`, `xs.fold(init, f)`.
  Each is bare: it takes bare functions and answers the value itself, not a `Result`, so
  no `??` follows the call. `filter` accepts an owning element type: it clones each
  element that it keeps
- Free functions over `List@(T)`: `map(xs, f)`, `filter(xs, pred)`, `fold(xs, init, f)`,
  and `compose(nom g, nom f)`
- Written in Sushi (a source stdlib module, no bitcode)
- Pass a lambda with typed parameters (`|i32 x| ...`) or a function reference

### Compression (`use <compression/zlib>`)

**zlib** - DEFLATE and the RFC 1950 container, written in Sushi (no C library, no FFI):
- `zlib_compress(src, level)`, `zlib_uncompress(src)` - the container, with an Adler-32 trailer
- `deflate_raw(src, level)`, `inflate_raw(src)` - a bare RFC 1951 stream
- `adler32(data)`, `e.text()` on a `ZError` - the checksum, and one stable line per error
- The decoder reads stored, fixed and dynamic blocks; the encoder emits stored and fixed
  only, so its ratio is short of a full encoder's

### I/O (`use <io/fs>`, `use <io/files>`)

**Console I/O:**
- `println()`, `print()` - Output with/without newline
- `stdin.readln()` - `Result@(Maybe@(string), IoError)`: one line, `Maybe.None` at the
  end of input, or an `Err` when the read fails
- `stdin`, `stdout`, `stderr` - `File` unit variables (`public var`) over descriptors 0, 1 and 2

**File I/O:**
- `open()` - Open files with Read/Write/Append modes
- One read: `read(max)` / `read_bytes(max)`; the whole file: `read_all()`; one line:
  `readln()`; writing: `write()` / `write_bytes()` / `writeln()`
- `close()` CONSUMES the handle, and is only needed where the failure has to be SEEN --
  a `File` closes itself when its owner leaves scope
- Every read, write, seek, `open()` and `close()` answers `IoError`; the path utilities
  and the `fd_*` primitives keep `FileError`
- `FileMode`, `FileError`, `IoError` and `SeekFrom` all come with `use <io/fs>`. The
  import of the home module of a predefined enum brings its name, and `<io/fs>`
  re-exports (`public use`) the home modules of the other three

**Buffered I/O** (`use <io/buf>`):
- `BufReader.new(nom src, cap)` / `BufWriter.new(nom dst, cap)` - one system call per WINDOW
- `r.lines()` answers a `Lines@(R)`, which `foreach` walks:
  `foreach(line?? in r.lines())`

### Math (`use <math>`)

All functions use a single polymorphic name (no type-suffixed variants):
- Absolute value / min / max: `abs()`, `min()`, `max()` (return the argument's type; a
  literal argument of `min`/`max` takes the type of the other argument)
- Floating-point (f64): `sqrt()`, `pow()`, `floor()`, `ceil()`, `round()`, `trunc()`
- Trigonometry: `sin()`, `cos()`, `tan()`, `asin()`, `acos()`, `atan()`, `atan2()`
- Hyperbolic: `sinh()`, `cosh()`, `tanh()`
- Exponential / logarithm: `exp()`, `exp2()`, `log()`, `log2()`, `log10()`
- Utility: `hypot()`
- Constants: `PI`, `E`, `TAU`

### Time (`use <time>`)

Sleep functions:
- `sleep(i64)` - Sleep for N seconds
- `msleep(i64)` - Sleep for N milliseconds
- `usleep(i64)` - Sleep for N microseconds
- `nanosleep(i64, i64)` - Nanosecond precision

Clocks:
- `now()` - Wall-clock time, in seconds since the Unix epoch (`i64`)
- `monotonic_ns()` - A monotonic clock in nanoseconds (`i64`), for intervals

Each sleep function answers `Result@(i32, StdError)`. The clocks cannot fail and answer a bare `i64`.

### Random (`use <random>`)

- `rand()` - A pseudo-random `u64`
- `rand_range(i32 min, i32 max)` - A pseudo-random `i32` in `[min, max)`
- `rand_f64()` - A pseudo-random `f64`
- `srand(u64 seed)` - Seed the generator
- Each function answers a bare value, not a `Result`. See [Random](stdlib/random.md)
  for the ranges that the functions give today

### Environment (`use <sys/env>`)

- `getenv()` - Get environment variable
- `setenv()` - Set environment variable

### Encoding (`use <encoding/msgpack>`)

- `decode(u8[] buf)` - Decode MessagePack bytes into a `MsgValue` tree
- `map_get()`, `map_index()`, `map_get_str()`, `map_get_bool()` - Read a map entry
- `show()` - Render a `MsgValue` as text

### I/O paths and contracts

- `<io/path>` - Lexical path functions: `join`, `basename`, `dirname`, `extension`,
  `normalize`. See [Path algebra](stdlib/io/path.md)
- `<io/contracts>` - The perks `Reader`, `Writer` and `Seek`. See
  [I/O contracts](stdlib/io/contracts.md)

### Networking (`use <net/tcp>`, `<net/udp>`, `<net/dns>`, `<net/ip>`, `<net/url>`)

- `connect(host, port)`, `listen(host, port, backlog)` - TCP streams and listeners
- UDP sockets, DNS resolution, IP addresses and URL splitting
- `<net/tcp>`, `<net/udp>`, `<net/dns>` and `<net/ip>` re-export `<net/error>`, so
  `NetError` comes with each of them. `<net/tcp>` and `<net/udp>` also re-export
  `<net/handle>` (`close_socket`)
- See the pages in the [Networking](#networking) list above

### Process (`use <sys/process>`)

- `getcwd()` - Get current working directory
- `chdir()` - Change working directory
- `exit()` - Terminate the process with an exit code
- `getpid()` - Get the process ID
- `getuid()` - Get the user ID
- `run()` - Spawn a program by argv (no shell), capturing stdout/stderr and exit code

## Design Principles

1. **Explicit error handling** - All fallible operations return `Result@(T, E)` or `Maybe@(T)`
2. **Memory safety** - RAII cleanup, no manual memory management
3. **Zero-cost abstractions** - Generics compile to concrete types
4. **UTF-8 by default** - Strings are UTF-8, methods are aware where needed
5. **Immutability** - String methods return new strings, arrays use RAII
6. **Type safety** - No null, no undefined behavior, exhaustive pattern matching

## Performance Notes

- **List@(T)**: Amortized O(1) push, O(n) insert/remove
- **HashMap@(K, V)**: O(1) average insert/get/remove, O(n) worst case
- **String methods**: All allocate new strings, O(n) for most operations
- **Arrays**: Direct memory access, bounds checked at runtime
- **Generics**: Monomorphized at compile-time (no runtime overhead)

## See Also

- [Language Reference](language-reference.md) - Core language features
- [Memory Management](memory-management.md) - RAII, borrowing, ownership
- [Generics](generics.md) - Generic types and functions
- [Getting Started](getting-started.md) - Installation and first program
