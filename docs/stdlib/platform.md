# Platform Detection System

[← Back to Architecture](../internals/architecture.md)

Internal documentation for Sushi's platform detection and cross-platform support.

## Overview

The platform detection system enables the compiler and standard library to select appropriate platform-specific implementations at build time. This allows Sushi to support macOS and Linux with a single codebase. Windows is a planned future target — no implementation exists yet (see [Supported Platforms](#supported-platforms) below).

## Platform Detection

### Target Triple Parsing

**File:** `backend/platform_detect.py`

The compiler parses LLVM target triples to determine the target platform.

**Target Triple Format:**
```
<arch>-<vendor>-<os>-<abi>
```

**Examples:**
- `arm64-apple-darwin22.0.0` - macOS on Apple Silicon
- `x86_64-pc-linux-gnu` - Linux on x86-64 with GNU libc
- `x86_64-pc-windows-msvc` - Windows on x86-64 with MSVC

### TargetPlatform Class

```python
@dataclass
class TargetPlatform:
    arch: str      # arm64, x86_64, i686, etc.
    vendor: str    # apple, pc, unknown
    os: str        # darwin, linux, windows
    abi: str       # gnu, musl, msvc, etc. (optional)

    @property
    def is_darwin(self) -> bool:
        """True for macOS"""
        return self.os == 'darwin'

    @property
    def is_linux(self) -> bool:
        """True for Linux"""
        return self.os == 'linux'
```

### Functions

#### parse_triple

```python
def parse_triple(triple: str) -> TargetPlatform:
    """Parse an LLVM target triple into components."""
```

Examples of the result:

```text
arm64-apple-darwin25.0.0 -> TargetPlatform(arm64, apple, darwin, '')
x86_64-pc-linux-gnu      -> TargetPlatform(x86_64, pc, linux, gnu)
```

A trailing OS version number is stripped and normalized: `darwin25.0.0` becomes `darwin`
(any `darwinN` form collapses to the bare `darwin` os string).

#### get_current_platform

```python
def get_current_platform() -> TargetPlatform:
    """Get the platform for the current compilation."""
    return parse_triple(llvm.get_default_triple())
```

Delegates straight to `llvmlite.binding.get_default_triple()` — there is no separate
`llvm.initialize()` call in the current implementation; that happens elsewhere in the
compiler's LLVM setup.

#### current_platform_name

```python
def current_platform_name() -> str:
    """The host platform as the short name a `.slib` records in its metadata.

    The single source of truth for that string: the library manifest writes it and
    the load-time platform check (CE3504) compares against it, so they must agree.
    """
    platform = get_current_platform()
    return "darwin" if platform.is_darwin else "linux" if platform.is_linux else "unknown"
```

Used by `compiler/pipeline.py` (`_check_library_platform`) to reject a binary or hybrid
`.slib` built for a different platform at load time (CE3504): its bitcode is
platform-specific, so a Darwin-built library does not link on a Linux host, and the
reverse. A source library (`--lib-kind source`, the default) carries no machine code,
so the check skips it and a source library loads on every platform.

## Standard Library Platform Support

### Platform-Specific Implementations

**Directory Structure:**
```
sushi_stdlib/src/_platform/
├── __init__.py       # get_platform_module() helper
├── README.md
├── posix/             # Shared POSIX implementations (used by BOTH darwin and linux)
│   ├── env.py         # getenv/setenv
│   ├── files.py       # stat/access/unlink/rename/open/read/write/close/mkdir/rmdir
│   ├── net.py         # the socket calls
│   ├── process.py     # getcwd/chdir/exit/getpid/getuid/tmpfile/fileno/waitpid/
│   │                  #   posix_spawnp + file_actions helpers/environ
│   ├── random.py      # random/srandom
│   ├── stdio.py       # FILE* type + parameterized stdin/stdout/stderr declarations
│   └── time.py        # nanosleep, clock_gettime
├── darwin/            # macOS
│   ├── env.py         # re-exports posix/env.py
│   ├── files.py       # macOS O_CREAT/O_TRUNC bit values
│   ├── net.py         # macOS socket constants and structure layouts
│   ├── process.py     # re-exports posix/process.py
│   ├── random.py      # re-exports posix/random.py
│   ├── stdio.py       # __stdinp/__stdoutp/__stderrp handle names
│   └── time.py        # re-exports posix/time.py, plus the macOS clock ids
└── linux/             # Linux: the same seven files
    ├── env.py
    ├── files.py       # Linux O_CREAT/O_TRUNC bit values
    ├── net.py         # Linux socket constants and structure layouts
    ├── process.py
    ├── random.py
    ├── stdio.py       # stdin/stdout/stderr handle names
    └── time.py        # re-exports posix/time.py, plus the Linux clock ids
```

There is no `windows/` directory.

Each of `darwin/` and `linux/` has one file per platform module. Most of them are thin
re-export shims over `posix/`, because those calls are identical POSIX libc functions on
both operating systems: `env.py`, `process.py` and `random.py` re-export `posix/`
unchanged, and `time.py` adds only the clock ids of the platform. Four modules have real,
distinct darwin/linux content: `stdio` (the stdin/stdout/stderr global symbol names),
`files` (the `open()` flag bit values for `O_CREAT`/`O_TRUNC`), `net` (the socket
constants and structure layouts) and the clock ids in `time`.

### Platform Module Helper

**File:** `sushi_lang/sushi_stdlib/src/_platform/__init__.py`

```python
def get_platform_module(module_name: str) -> ModuleType:
    """Import the platform-specific module of the build platform."""
    platform = get_current_platform()

    if platform.is_darwin:
        platform_name = 'darwin'
    elif platform.is_linux:
        platform_name = 'linux'
    else:
        raise RuntimeError(f"Unsupported platform: {platform.os}")

    module_path = f"{__name__}.{platform_name}.{module_name}"

    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError:
        raise NotImplementedError(
            f"Platform module '{module_name}' not implemented for {platform_name}. "
            f"Expected: sushi_lang/sushi_stdlib/src/_platform/{platform_name}/{module_name}.py"
        ) from None
```

The module path is built from the package's own `__name__`, so a platform module is imported under
one name only (`sushi_lang.sushi_stdlib.src._platform.<os>.<module>`), and the loader changes no
`sys.path`. A platform that is not macOS or Linux is one `RuntimeError`; there is no `windows`
branch.

### Usage in Standard Library Modules

**Example:** `sushi_stdlib/src/sys/env/functions.py` (backs the `sys/env` unit; the
`getenv`/`setenv` builtins used from Sushi source)

```python
from sushi_lang.sushi_stdlib.src._platform import get_platform_module

_platform_env = get_platform_module('env')

def generate_getenv(module: ir.Module) -> None:
    """Generate getenv function: getenv(string key) -> Maybe@(string)"""
    # Declare external functions
    libc_getenv = _platform_env.declare_getenv(module)
    # ... builds the sushi_getenv IR using the platform-specific declaration ...
```

`sushi_stdlib/src/time/sleep.py` (backs `use <time>`) follows the identical pattern with
`get_platform_module('time')`, and `sushi_stdlib/src/error_emission.py` does the same
with `get_platform_module('stdio')` to name the `stderr` handle a runtime error prints
to. The platform module is resolved once per generator module, at import time, and
reused for every function it generates.

## `<sys/platform>`: the platform constants for Sushi source

A module written in Sushi cannot choose a value by platform: Sushi has no conditional
compilation, and a dead branch still declares its extern, so at `--opt none` its call
survives and fails to link. So the compiler selects a FILE. `use <sys/platform>` compiles
the bundled file for the host:

| Host | File |
|---|---|
| macOS on Apple Silicon | `src_sushi/_platform/darwin_arm64.sushi` |
| macOS on x86_64 | `src_sushi/_platform/darwin_x86_64.sushi` |
| Linux on x86_64 | `src_sushi/_platform/linux_x86_64.sushi` |

Any other host is `CE3021`. Linux on aarch64 has no file, because Linux ships and is
tested as x86_64 only.

Each file holds `public const` declarations and nothing else, and every file declares the
same names of the same types in the same order:

- the `open` flags (`O_RDONLY` ... `O_APPEND`) and the `lseek` whences;
- the `struct stat` size, the offsets and widths of `st_mode`, `st_size`, the times, the
  file type bits (`S_IFMT`, `S_IFREG`, ...), and `dirent.d_name`;
- the link names of `stat`, `lstat` and `readdir` (`stat$INODE64` on macOS x86_64);
- the clock ids, and the `timespec` and `timeval` layouts;
- the socket constants: families, types, levels, options, the `addrinfo` and `sockaddr`
  offsets, and the SIGPIPE pair (`SO_NOSIGPIPE`, `MSG_NOSIGNAL`, 0 where a platform has
  none);
- the errno numbers the standard library maps to an error variant.

```sushi
use <sys/platform>

unsafe external "C" as libc because "reading file metadata":
    fn malloc(i64 n) ptr = "malloc"
    fn free(ptr p) ~ = "free"
    fn stat(string path, ptr buf) i32 = STAT_SYMBOL

fn main() i32:
    let ptr buf = libc.malloc(STAT_SIZE as i64)
    let i32 rc = libc.stat("/dev/null", buf)
    let i64 size = buf.load_i64(ST_SIZE_OFFSET)
    println("stat: {rc}, size: {size}")
    libc.free(buf)
    return Result.Ok(0)
```

**Where the values come from.** `tests/platform_probe/probe.c` prints a whole platform
file, with `offsetof`, `sizeof` and the C headers of the host it is compiled on. To make
or refresh a file, compile the probe on that host: `cc tests/platform_probe/probe.c -o
probe && ./probe > sushi_lang/sushi_stdlib/src_sushi/_platform/<host>.sushi`. The macOS
x86_64 file is made with `cc -arch x86_64` (Rosetta runs it), and the Linux file in the
`linux/amd64` container. `tests/unit/test_platform_files.py` compiles the probe on the
host of the test run and requires the host's file to be exactly what it prints, and it
requires the three files to declare the same names. So the macOS CI checks the macOS
arm64 file and the Linux CI checks the Linux file.

## Platform-Organized Build Outputs

### Distribution Structure

Standard library bytecode is organized by platform:

```
sushi_stdlib/dist/
├── darwin/                    # macOS builds
│   ├── collections/strings.bc
│   ├── core/primitives.bc
│   ├── io/files.bc
│   ├── math.bc
│   ├── net/socket.bc
│   ├── random.bc
│   ├── sys/env.bc
│   ├── sys/process.bc
│   └── time.bc
└── linux/                     # Linux builds — same unit layout as darwin/, built
    └── ...                    #   separately on a Linux host (see Cross-Compilation below)
```

There is no `windows/` subdirectory; `sushi_stdlib/build.py`'s own `--platform` flag only
accepts `darwin` or `linux` as choices, so there is no way to even ask it to produce one.
`core/results` and `core/maybe` are intentionally absent from this list — they are emitted
inline per call site rather than built as standalone stdlib units, because monomorphizing
them ahead of time for every possible user type is impractical.

### Compiler Module Selection

At compile time, the compiler:

1. Detects the host platform via `get_current_platform()`
2. Selects the matching `sushi_stdlib/dist/{platform}/` directory
3. Links the platform-specific `.bc` files for every `use <...>` unit the program imports

**Real implementation:** `backend/stdlib_linker.py`, class `StdlibLinker`

```python
def _detect_platform(self) -> str:
    """Detect current platform for stdlib selection.

    Returns:
        Platform name: "darwin", "linux", or "unknown".
    """
    from sushi_lang.backend.platform_detect import get_current_platform

    platform = get_current_platform()
    if platform.is_darwin:
        return "darwin"
    elif platform.is_linux:
        return "linux"
    else:
        return "unknown"
```

`self.platform` is combined with `self.stdlib_dir` (`sushi_stdlib/dist/`) to resolve both
individual units (`"io/files"` -> `sushi_stdlib/dist/darwin/io/files.bc`) and directory
imports (`"io"` -> every `.bc` file under `sushi_stdlib/dist/darwin/io/`). There is no
`windows` case here either — a Windows host resolves to `"unknown"`, and `_resolve_stdlib_unit`
then fails to find the (nonexistent) `sushi_stdlib/dist/unknown/` directory with a clear
error rather than silently picking a wrong platform's bitcode.

## Supported Platforms

### macOS (darwin)

**Status:** Fully supported. The primary development platform.

**Architecture Support:**
- `arm64` (Apple Silicon)
- `x86_64` (Intel Macs)

**Implementation Location:** `sushi_stdlib/src/_platform/darwin/`, backed by
`sushi_stdlib/src/_platform/posix/` for everything that is not macOS-specific.

**Features:**
- POSIX `getenv()`/`setenv()` (via `posix/env.py`)
- POSIX `nanosleep()` (via `posix/time.py`)
- File I/O: `stat`/`access`/`unlink`/`rename`/`open`/`read`/`write`/`close`/`mkdir`/
  `rmdir` (via `posix/files.py`), plus macOS-specific `O_CREAT`/`O_TRUNC` bit values
  (`darwin/files.py`)
- Process control: `getcwd`/`chdir`/`exit`/`getpid`/`getuid`/`tmpfile`/`fileno`/
  `waitpid`/`posix_spawnp` (+ file-action helpers)/`environ` (via `posix/process.py`)
- `random()`/`srandom()` (via `posix/random.py`)
- stdio handles exposed as `__stdinp`/`__stdoutp`/`__stderrp` (macOS's double-underscore
  libc symbol names, distinct from Linux — `darwin/stdio.py`)
- BSD semantics for system calls

### Linux

**Status:** Fully supported. CI testing runs on Linux.

**Architecture Support:**
- `x86_64` (64-bit). The project tests and ships Linux on `x86_64` only.

**Implementation Location:** `sushi_stdlib/src/_platform/linux/`, backed by
`sushi_stdlib/src/_platform/posix/` for everything that is not Linux-specific.

**Features:**
- POSIX `getenv()`/`setenv()`, `nanosleep()`, file I/O, and process control — same
  `posix/` implementations macOS uses (identical libc surface on both)
- Linux-specific `O_CREAT`/`O_TRUNC` bit values (`linux/files.py` — the numeric flag
  values differ from macOS's even though the function signatures match)
- stdio handles exposed as plain `stdin`/`stdout`/`stderr` (glibc/musl symbol names,
  no double-underscore prefix — `linux/stdio.py`)
- GNU/Linux semantics

### Windows

**Status:** Not supported. No implementation exists.

There is no `sushi_stdlib/src/_platform/windows/` directory, and no path through the
compiler currently produces a working Windows build:

- `get_platform_module()` has no Windows branch: a Windows host is a `RuntimeError`
  ("Unsupported platform") — see [Platform Module Helper](#platform-module-helper) above.
- `StdlibLinker._detect_platform()` (`backend/stdlib_linker.py`) has no Windows case at
  all; a Windows host resolves to `"unknown"`.
- `sushi_stdlib/build.py`'s `--platform` argument only accepts `darwin` or `linux` as
  choices, and its `main()` prints `ERROR: Unsupported platform` and exits with status 1
  for anything else.

Windows is a plausible future target — the codebase's platform-abstraction layer
(`_platform/`) was clearly designed with a third platform in mind — but as of this
writing it is aspirational only. Anyone relying on the doc's older wording ("Windows...
partial support") should not expect any Windows-specific code path to run.

## Adding Platform-Specific Functionality

The following steps describe the actual pattern used by the existing `env`, `time`,
`files`, `process`, `random`, and `stdio` modules. Only Windows support is out of reach
today — there is nothing to route to.

### Step 1: Default to `posix/`, Only Split Out What Actually Differs

Most new functionality should start as a single implementation in
`sushi_stdlib/src/_platform/posix/myfeature.py` — the same function signature is a
correct declaration on both macOS and Linux for nearly everything in libc. Only create
separate `darwin/myfeature.py` and `linux/myfeature.py` files if the two operating
systems genuinely disagree (as `stdio.py`'s handle names and `files.py`'s `O_CREAT`/
`O_TRUNC` bit values do):

```
sushi_stdlib/src/_platform/
├── posix/
│   └── myfeature.py       # Shared POSIX implementation (the common case)
├── darwin/
│   └── myfeature.py       # Only if macOS genuinely differs — re-export from
│                           #   posix/myfeature.py otherwise (see darwin/__init__.py)
└── linux/
    └── myfeature.py       # Only if Linux genuinely differs
```

Windows has no directory to add a file to yet; adding one alone would not make
`get_platform_module('myfeature')` succeed on Windows, since `StdlibLinker` and
`build.py` do not recognize a `windows` platform at the levels above `_platform/`.

### Step 2: Implement the IR Generation

**Example:** `sushi_stdlib/src/_platform/posix/myfeature.py`

```python
import llvmlite.ir as ir

def declare_platform_function(module: ir.Module) -> ir.Function:
    """Declare the libc function this feature depends on."""
    func_type = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()])
    func = ir.Function(module, func_type, name="platform_specific_func")
    return func
```

If macOS and Linux truly diverge, give `darwin/myfeature.py` and `linux/myfeature.py`
each their own `declare_platform_function`, matching the pattern in `darwin/files.py` /
`linux/files.py` (same function names, different constant values or symbol names).

### Step 3: Create the Unified Interface

**File:** `sushi_stdlib/src/myfeature.py` (or a subpackage, e.g. `sys/myfeature/functions.py`)

```python
from sushi_lang.sushi_stdlib.src._platform import get_platform_module

# Load platform-specific implementation
_platform_myfeature = get_platform_module('myfeature')

def generate_myfeature_ir(module, builder):
    """Generate IR using the platform-specific implementation."""
    func = _platform_myfeature.declare_platform_function(module)
    # ...
```

### Step 4: Build the Bytecode — On Each Actual Host

`sushi_stdlib/build.py`'s `--platform` flag only picks the **output directory name**; the
IR it generates always reflects the *current host* platform (see `build_all()`'s
docstring). Running it on macOS cannot produce a working `dist/linux/` build — you must
actually run the build on a Linux machine (or in Linux CI) to produce Linux bitcode:

```bash
# On macOS — produces dist/darwin/myfeature.bc
python sushi_lang/sushi_stdlib/build.py

# On Linux — produces dist/linux/myfeature.bc
python sushi_lang/sushi_stdlib/build.py
```

`./sushic --build-stdlib` triggers the same `build_all()` path for the host platform; it
is not a way to target a different platform either.

### Step 5: Nothing Else to Update

Once the `.bc` file lands under `sushi_stdlib/dist/{platform}/`, `StdlibLinker` picks it
up automatically the next time a `use <myfeature>` unit is imported — there is no
separate compiler-linkage step to hand-edit. See
[Compiler Module Selection](#compiler-module-selection) above for how resolution works.

## Cross-Compilation Considerations

**Cross-compilation is not currently supported.** There is no `--target` flag (or any
equivalent) on `./sushic` — checked against the actual argument list in
`compiler/cli.py`, which has no target-triple override of any kind. The compiler always
targets the host it runs on, discovered via `llvmlite.binding.get_default_triple()`
(see [get_current_platform](#get_current_platform) above).

Two things would need to change before cross-compilation could work:
- `sushi_stdlib/build.py` generates IR that reflects the *host* platform regardless of
  the `--platform` flag (see Step 4 above) — it is not a cross-compiler today.
- A binary or hybrid `.slib` is platform-tagged, and a library of that kind built on one
  platform is rejected (**CE3504**) when loaded on another (`compiler/pipeline.py`,
  `_check_library_platform`). A source `.slib` (the default kind) is not checked and
  loads on every platform.

Producing genuinely portable output would require an actual cross-compiling stdlib build
(generating Linux-shaped IR from a macOS host, or vice versa) plus a target-triple CLI
flag threaded through `StdlibLinker` and the library-platform check — none of which
exists yet.

## Testing Platform-Specific Code

There is currently no platform-scoped test organization. `tests/run_tests.py` has no
`--platform` flag, and there are no `tests/darwin/` or `tests/linux/` directories —
tests simply run on whatever host invokes them. In practice that means: developed and
run locally on macOS, and re-run in CI on Linux, with the same
test files exercised on both. The one place the test runner itself branches on
`sys.platform` is unrelated to test selection — it is picking the right file extension
(`.dylib` vs `.so`) for the precompiled leak-check malloc interposer.

A test that is only meaningful on one platform today has to guard itself at the Sushi
source or Python test-harness level; the infrastructure for skipping whole test files by
host platform does not exist.

## See Also

- [Architecture](../internals/architecture.md) - Compiler architecture overview
- [Backend](../internals/backend.md) - LLVM code generation
- [Standard Library](../standard-library.md) - Available stdlib modules
