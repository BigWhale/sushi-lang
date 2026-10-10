"""Test metadata parsing for the Sushi language test framework."""

import hashlib
import re
import sys
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
from pathlib import Path


def _warn(message: str) -> None:
    """Every diagnostic this module prints, on stderr.

    `run_tests.py --json` writes the corpus report to stdout and the badge job pipes
    that straight into a file it json.loads, so a warning on stdout is an unparseable
    report. Collection knows nothing about the mode, so the channel is the fix rather
    than a guard.
    """
    print(f"Warning: {message}", file=sys.stderr)


@dataclass
class TestMetadata:
    """Metadata for a test file specifying expected runtime behavior."""

    # Runtime expectations
    expect_runtime_exit: Optional[int] = None
    expect_stdout_contains: Optional[List[str]] = None
    expect_stdout_exact: Optional[str] = None
    expect_stderr_contains: Optional[List[str]] = None
    expect_stderr_empty: bool = False

    # Compilation diagnostics expectations (error/warning categories).
    # Enforced on the compilation path, not the runtime path.
    expect_error_code: Optional[List[str]] = None
    # The WHOLE set of codes the compiler printed, warnings included (#959). None when
    # the fixture does not say; EXPECT_ERROR_CODE is a substring check and cannot pin it.
    expect_error_codes_exact: Optional[List[str]] = None

    # Opt-in leak assertion, enforced by every enhanced run that executes the test.
    # --leaks-only narrows the selection to the tests carrying it; it does not decide
    # whether it is honoured.
    expect_no_leaks: bool = False

    # Opt-in DESCRIPTOR assertion, the handle half of the same gate. The malloc
    # interposer counts bytes and cannot see a file or a socket, so EXPECT_NO_LEAKS
    # gives a handle test no coverage at all (HANDLES.md, Phase 2).
    expect_no_open_fds: bool = False

    # Test behavior flags
    requires_runtime: bool = False
    timeout_seconds: int = 10
    cmd_args: Optional[str] = None  # Command-line arguments for runtime test
    stdin_input: Optional[str] = None  # Standard input to provide to the test
    test_env: Optional[Dict[str, str]] = None  # Env vars to set for the runtime binary
    test_cwd: Optional[str] = None  # Working directory to run the runtime binary in

    # Extra flags appended to the ./sushic command line. A diagnostic behind a compiler
    # flag has no other way to be exercised by a .sushi fixture.
    compiler_flags: Optional[List[str]] = None
    # Extra flags for EVERY library build of the BUILD_LIB family, never for the
    # compilation of the fixture itself.
    lib_flags: Optional[List[str]] = None

    # A rebuild fixture (#988): a fixture whose directory holds a `v2/` directory. The
    # units the SECOND compilation reports `[rebuilt]` and `[cached]`, and the stdout of
    # the FIRST binary. None when the fixture does not say.
    expect_rebuilt: Optional[List[str]] = None
    expect_cached: Optional[List[str]] = None
    expect_stdout_exact_before_rebuild: Optional[str] = None

    # Start `sushic` from (a copy of) the fixture's directory, with a relative source path.
    run_in_fixture_dir: bool = False
    # Source `.slib` files the runner builds in the fixture's copy before each compilation.
    build_libs: Optional[List[str]] = None
    # Binary `.slib` files built the same way, on the same SUSHI_LIB_PATH directory.
    build_libs_binary: Optional[List[str]] = None
    # Hybrid `.slib` files built the same way, on the same SUSHI_LIB_PATH directory.
    build_libs_hybrid: Optional[List[str]] = None
    # Source `.slib` files that must build with exit 1 and exactly these warning codes.
    build_libs_warns: Optional[List[Tuple[str, List[str]]]] = None
    # Every BUILD_LIB, BUILD_LIB_BINARY, BUILD_LIB_HYBRID and BUILD_LIB_WARNS as (kind,
    # source, `.slib` file name, version or None, warning codes or None), in written
    # order: the order the runner builds them in.
    library_builds: Optional[List[Tuple[str, str, str, Optional[str],
                                        Optional[List[str]]]]] = None
    # Sushi-source stdlib modules the compiler registers from the fixture's copy: name -> path.
    stdlib_modules: Optional[Dict[str, str]] = None
    # Source `.slib` files built at a path inside the copy: (source, target), both relative.
    build_libs_at: Optional[List[Tuple[str, str]]] = None
    # A relative `--cache-dir`, spelled as given, for a fixture that runs in its own directory.
    fixture_cache_dir: Optional[str] = None
    # Paths of the copy read after the fixture's last compiler invocation.
    expect_paths_exist: Optional[List[str]] = None
    expect_paths_absent: Optional[List[str]] = None
    # Paths of the copy that must exist after the compilation and BEFORE THEN_CLEAN_CACHE.
    expect_paths_exist_before_clean: Optional[List[str]] = None
    # A last invocation, `sushic --clean-cache`: "bare", or "source" (with the source and -o).
    then_clean_cache: Optional[str] = None
    # The `-o` path, relative to the fixture's copy; the runner never creates its parent.
    output_path: Optional[str] = None
    # Where `--lib` stood in COMPILER_FLAGS, held until the parse knows whether OUTPUT_PATH
    # is there. None when the fixture does not spell it.
    held_lib_flag: Optional[int] = None
    # What the emitted IR of one function must hold or lack: (function, text, holds), in
    # written order. The runner asks the compiler for the IR when the list is not empty.
    ir_expectations: Optional[List[Tuple[str, str, bool]]] = None
    # A directive value the parser could not read; the runner fails a fixture that has one.
    directive_errors: Optional[List[str]] = None

    def __post_init__(self):
        """Post-initialization processing."""
        if self.expect_stdout_contains is None:
            self.expect_stdout_contains = []
        if self.expect_stderr_contains is None:
            self.expect_stderr_contains = []
        if self.expect_error_code is None:
            self.expect_error_code = []
        if self.test_env is None:
            self.test_env = {}
        if self.compiler_flags is None:
            self.compiler_flags = []
        if self.lib_flags is None:
            self.lib_flags = []
        if self.build_libs is None:
            self.build_libs = []
        if self.build_libs_binary is None:
            self.build_libs_binary = []
        if self.build_libs_hybrid is None:
            self.build_libs_hybrid = []
        if self.build_libs_warns is None:
            self.build_libs_warns = []
        if self.library_builds is None:
            self.library_builds = []
        if self.stdlib_modules is None:
            self.stdlib_modules = {}
        if self.build_libs_at is None:
            self.build_libs_at = []
        if self.expect_paths_exist is None:
            self.expect_paths_exist = []
        if self.expect_paths_absent is None:
            self.expect_paths_absent = []
        if self.expect_paths_exist_before_clean is None:
            self.expect_paths_exist_before_clean = []
        if self.ir_expectations is None:
            self.ir_expectations = []
        if self.directive_errors is None:
            self.directive_errors = []

    @property
    def libs_on_the_path(self) -> bool:
        """A library the runner builds into the directory it puts on SUSHI_LIB_PATH."""
        return bool(self.library_builds)

    @property
    def reads_the_copy(self) -> bool:
        """A directive that names a path in the fixture's copy, so the fixture needs one."""
        return bool(self.build_libs_at or self.fixture_cache_dir is not None
                    or self.expect_paths_exist or self.expect_paths_absent
                    or self.expect_paths_exist_before_clean
                    or self.then_clean_cache is not None or self.directive_errors
                    or self.output_path is not None)

    @property
    def declares_a_rebuild(self) -> bool:
        return (self.expect_rebuilt is not None or self.expect_cached is not None
                or self.expect_stdout_exact_before_rebuild is not None)

        # If any runtime expectations are set, this test requires runtime validation
        if (self.expect_runtime_exit is not None or
            self.expect_stdout_contains or
            self.expect_stdout_exact is not None or
            self.expect_stderr_contains or
            self.expect_stderr_empty):
            self.requires_runtime = True


# Flags the RUNNER spells: they decide the output path, the build kind and the cache, so
# a fixture that changed one would break the run rather than test anything.
RUNNER_OWNED_FLAGS = frozenset({
    '-o', '--lib', '--lib-info', '--clean-cache', '--build-stdlib', '--cache-dir',
})

# The two flags the runner writes for each library build. `LIB_FLAGS` refuses them, because
# a later one would win silently; `COMPILER_FLAGS` may spell them, as a fixture's own
# build is no library build.
LIB_BUILD_FLAGS = frozenset({'--lib-kind', '--lib-version'})

# The one runner-owned flag a fixture may spell once it names its own output (OUTPUT_PATH):
# the build kind is then the fixture's, because the runner no longer picks the file.
LIB_FLAG = '--lib'


# The directory beside a rebuild fixture whose files replace their namesakes before the
# second compilation. It is data, never a fixture of its own.
REBUILD_DIR = "v2"


def is_rebuild_fixture(test_file: Path) -> bool:
    return (Path(test_file).parent / REBUILD_DIR).is_dir()


def _unquote(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _text(value: str) -> str:
    """A quoted-string value, with `\\n` and `\\t` read as escapes."""
    return _unquote(value).replace('\\n', '\n').replace('\\t', '\t')


def _split(value: str) -> List[str]:
    """A comma/space separated list."""
    return [token for token in re.split(r'[,\s]+', value) if token]


def _flag_value(rest: str) -> Optional[bool]:
    """A bare `NAME` is true; `NAME: true|yes|1` is true; `NAME: <other>` is false."""
    rest = rest.lstrip()
    if rest == '':
        return True
    if rest.startswith(':'):
        return rest[1:].strip().lower() in ('true', 'yes', '1')
    return None


def header_block(lines: List[str]) -> List[str]:
    """The leading comment block: every line before the first line of CODE."""
    header = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            break
        header.append(line)
    return header


class DirectiveBlockError(ValueError):
    """The leading comment block of a fixture holds a byte that is not UTF-8."""


def directive_block(test_file: Path) -> List[str]:
    """The leading comment block of a file, read as BYTES and decoded alone.

    A later line may hold any byte (a fixture about a source that is not UTF-8 holds
    one), so only the block the runner reads must be UTF-8.
    """
    lines = []
    for number, raw in enumerate(Path(test_file).read_bytes().split(b'\n'), start=1):
        stripped = raw.strip()
        if stripped and not stripped.startswith(b'#'):
            break
        try:
            lines.append(raw.decode('utf-8'))
        except UnicodeDecodeError as e:
            raise DirectiveBlockError(
                f"byte 0x{raw[e.start]:02x} on line {number} of the directive block is not "
                f"valid UTF-8") from None
    return lines


def _int_into(field_name: str, directive: str):
    def handle(metadata: TestMetadata, value: str, test_file: Path) -> None:
        try:
            setattr(metadata, field_name, int(value))
        except ValueError:
            _warn(f"Invalid {directive} value in {test_file}: {value}")
    return handle


def _extend(field_name: str, read):
    def handle(metadata: TestMetadata, value: str, test_file: Path) -> None:
        read_value = read(value)
        current = getattr(metadata, field_name)
        if isinstance(read_value, list):
            setattr(metadata, field_name, (current or []) + read_value)
        else:
            current.append(read_value)
    return handle


def _set(field_name: str, read):
    def handle(metadata: TestMetadata, value: str, test_file: Path) -> None:
        setattr(metadata, field_name, read(value))
    return handle


def _exact_codes(metadata: TestMetadata, value: str, test_file: Path) -> None:
    codes = _split(_unquote(value))
    if not codes:
        _warn(f"Empty EXPECT_ERROR_CODES_EXACT in {test_file}")
    metadata.expect_error_codes_exact = (metadata.expect_error_codes_exact or []) + codes


def _refuses_runner_flag(token: str, directive: str, test_file: Path,
                         owned: frozenset = RUNNER_OWNED_FLAGS) -> bool:
    """A flag the runner owns, refused with a printed warning. One check for every
    directive that passes flags to `sushic`."""
    if token in owned:
        _warn(f"{token} is the runner's to spell in {test_file}; {directive} ignored it")
        return True
    return False


def _compiler_flags(metadata: TestMetadata, value: str, test_file: Path) -> None:
    for token in _split(value):
        if token == LIB_FLAG:
            metadata.held_lib_flag = len(metadata.compiler_flags)
            continue
        if not _refuses_runner_flag(token, "COMPILER_FLAGS", test_file):
            metadata.compiler_flags.append(token)


def _lib_flags(metadata: TestMetadata, value: str, test_file: Path) -> None:
    owned = RUNNER_OWNED_FLAGS | LIB_BUILD_FLAGS
    metadata.lib_flags.extend(
        token for token in _split(value)
        if not _refuses_runner_flag(token, "LIB_FLAGS", test_file, owned))


def _test_env(metadata: TestMetadata, value: str, test_file: Path) -> None:
    # One KEY=VALUE per directive; the directive may be repeated to set several
    # variables. Lets a test pin HOME/USER/etc. instead of baking the developer's host
    # environment into an expected-stdout snapshot.
    if '=' in value:
        key, val = value.split('=', 1)
        metadata.test_env[key.strip()] = val.strip()
    else:
        _warn(f"Invalid TEST_ENV value in {test_file}: {value}")


def _stdlib_module(metadata: TestMetadata, value: str, test_file: Path) -> None:
    name, sep, path = value.partition('=')
    if sep and name.strip() and path.strip():
        metadata.stdlib_modules[name.strip()] = path.strip()
    else:
        _warn(f"Invalid STDLIB_MODULE value in {test_file}: {value}")


def _library_build(field_name: str, kind: str):
    """A BUILD_LIB-family directive: the kind's own list, and the one ordered list.

    `x.sushi` builds `x.slib` at the runner's version. `x.sushi -> y.slib` builds the
    same source under another file name, so the library name is not the unit name, and
    `x.sushi @ 0.2.0` stamps that version. The two forms combine, `->` first.
    """
    def handle(metadata: TestMetadata, value: str, test_file: Path) -> None:
        text, at, version = _unquote(value).partition('@')
        version = version.strip() if at else None
        source, sep, target = text.partition('->')
        source, target = source.strip(), target.strip()
        if sep or at:
            if (not source or len(_split(source)) != 1 or (at and not version)
                    or (sep and (not target.endswith('.slib')
                                 or len(Path(target).parts) != 1))):
                metadata.directive_errors.append(
                    f"a {kind} library build takes `<source>`, `<source> -> <name>.slib` "
                    f"or either with `@ <version>`, not {value!r}")
                return
            pairs = [(source, target or f"{Path(source).stem}.slib")]
        else:
            pairs = [(name, f"{Path(name).stem}.slib") for name in _split(source)]
        getattr(metadata, field_name).extend(name for name, _ in pairs)
        metadata.library_builds.extend((kind, name, slib, version, None)
                                       for name, slib in pairs)
    return handle


_WARNING_CODE = re.compile(r"CW\d{4}")


LIBRARY_KINDS = ("source", "binary", "hybrid")


def _build_lib_warns(metadata: TestMetadata, value: str, test_file: Path) -> None:
    """`x.sushi -> CW3003`, or `x.sushi binary -> CW3003` for another library kind."""
    head, sep, codes = _unquote(value).partition('->')
    words, codes = _split(head), _split(codes)
    kind = words[1] if len(words) == 2 else "source"
    if (not sep or len(words) not in (1, 2) or kind not in LIBRARY_KINDS or not codes
            or not all(_WARNING_CODE.fullmatch(code) for code in codes)):
        metadata.directive_errors.append(
            "BUILD_LIB_WARNS takes `<source> [source|binary|hybrid] -> <warning code>"
            f"[, <warning code>...]`, not {value!r}")
        return
    metadata.build_libs_warns.append((words[0], codes))
    metadata.library_builds.append(
        (kind, words[0], f"{Path(words[0]).stem}.slib", None, codes))


def _build_lib_at(metadata: TestMetadata, value: str, test_file: Path) -> None:
    source, sep, target = _unquote(value).partition('->')
    if sep and source.strip() and target.strip():
        metadata.build_libs_at.append((source.strip(), target.strip()))
    else:
        metadata.directive_errors.append(
            f"BUILD_LIB_AT takes `<source> -> <target>`, not {value!r}")


CLEAN_CACHE_FORMS = ("bare", "source")


def _then_clean_cache(metadata: TestMetadata, value: str, test_file: Path) -> None:
    form = _unquote(value).strip().lower()
    if form in CLEAN_CACHE_FORMS:
        metadata.then_clean_cache = form
    else:
        metadata.directive_errors.append(
            f"THEN_CLEAN_CACHE takes {' or '.join(CLEAN_CACHE_FORMS)}, not {value!r}")


def _output_path(metadata: TestMetadata, value: str, test_file: Path) -> None:
    path = _unquote(value).strip()
    if not path or Path(path).is_absolute() or ".." in Path(path).parts:
        metadata.directive_errors.append(
            f"OUTPUT_PATH takes a relative path inside the fixture's copy, not {value!r}")
    else:
        metadata.output_path = path


def _ir_expectation(directive: str, holds: bool):
    """`EXPECT_IR_HOLDS: <function> <text>` / `EXPECT_IR_LACKS: <function> <text>`."""
    def handle(metadata: TestMetadata, value: str, test_file: Path) -> None:
        function, _, text = _unquote(value).strip().partition(' ')
        text = _unquote(text.strip())
        if not function or not text:
            metadata.directive_errors.append(
                f"{directive} takes `<function> <text>`, not {value!r}")
            return
        metadata.ir_expectations.append((function, text, holds))
    return handle


def _release_lib_flag(metadata: TestMetadata, test_file: Path) -> None:
    """`--lib` joins the flags beside OUTPUT_PATH, and is refused without it."""
    if metadata.held_lib_flag is None:
        return
    if metadata.output_path is not None:
        metadata.compiler_flags.insert(metadata.held_lib_flag, LIB_FLAG)
    else:
        _warn(f"{LIB_FLAG} is the runner's to spell in {test_file} without OUTPUT_PATH; "
              f"COMPILER_FLAGS ignored it")


# Every directive that takes a value (`NAME: value`), and its handler. ONE table, so a
# directive the parser knows is a row here and nowhere else.
VALUED_DIRECTIVES = {
    'EXPECT_RUNTIME_EXIT': _int_into('expect_runtime_exit', 'EXPECT_RUNTIME_EXIT'),
    'EXPECT_STDOUT_CONTAINS': _extend('expect_stdout_contains', _text),
    'EXPECT_STDOUT_EXACT': _set('expect_stdout_exact', _text),
    'EXPECT_STDERR_CONTAINS': _extend('expect_stderr_contains', _text),
    'EXPECT_STDERR_EMPTY': _set('expect_stderr_empty',
                                lambda v: v.lower() in ('true', 'yes', '1')),
    # A comma/space separated list, and the directive may repeat for multi-error compiles.
    'EXPECT_ERROR_CODE': _extend('expect_error_code', lambda v: _split(_unquote(v))),
    'EXPECT_ERROR_CODES_EXACT': _exact_codes,
    'COMPILER_FLAGS': _compiler_flags,
    'TIMEOUT_SECONDS': _int_into('timeout_seconds', 'TIMEOUT_SECONDS'),
    # Stored as-is; the runner splits it on whitespace.
    'CMD_ARGS': _set('cmd_args', str),
    'STDIN_INPUT': _set('stdin_input', _text),
    'TEST_ENV': _test_env,
    # Working directory to run the binary in, so getcwd()-style output is
    # host-independent (e.g. TEST_CWD: / yields a deterministic "/").
    'TEST_CWD': _set('test_cwd', str),
    'EXPECT_REBUILT': _extend('expect_rebuilt', lambda v: _split(_unquote(v))),
    'EXPECT_CACHED': _extend('expect_cached', lambda v: _split(_unquote(v))),
    'EXPECT_STDOUT_EXACT_BEFORE_REBUILD': _set('expect_stdout_exact_before_rebuild', _text),
    'BUILD_LIB': _library_build('build_libs', 'source'),
    'BUILD_LIB_BINARY': _library_build('build_libs_binary', 'binary'),
    'BUILD_LIB_HYBRID': _library_build('build_libs_hybrid', 'hybrid'),
    'BUILD_LIB_WARNS': _build_lib_warns,
    'LIB_FLAGS': _lib_flags,
    'STDLIB_MODULE': _stdlib_module,
    'BUILD_LIB_AT': _build_lib_at,
    'FIXTURE_CACHE_DIR': _set('fixture_cache_dir', lambda v: _unquote(v).strip()),
    'EXPECT_PATH_EXISTS': _extend('expect_paths_exist', lambda v: _split(_unquote(v))),
    'EXPECT_PATH_ABSENT': _extend('expect_paths_absent', lambda v: _split(_unquote(v))),
    'EXPECT_PATH_EXISTS_BEFORE_CLEAN': _extend('expect_paths_exist_before_clean',
                                               lambda v: _split(_unquote(v))),
    'THEN_CLEAN_CACHE': _then_clean_cache,
    'OUTPUT_PATH': _output_path,
    'EXPECT_IR_HOLDS': _ir_expectation('EXPECT_IR_HOLDS', True),
    'EXPECT_IR_LACKS': _ir_expectation('EXPECT_IR_LACKS', False),
}

# Every directive that is a flag: bare `NAME` is true, `NAME: true|yes|1` sets it.
FLAG_DIRECTIVES = {
    'EXPECT_NO_LEAKS': 'expect_no_leaks',
    'EXPECT_NO_OPEN_FDS': 'expect_no_open_fds',
    'RUN_IN_FIXTURE_DIR': 'run_in_fixture_dir',
}

# A header line that starts with one of these names IS a directive: the parser reads it,
# or the fixture fails. A line that starts with a diagnostic code is prose.
_DIRECTIVE_NAME = re.compile(r"[A-Z][A-Z0-9_]{3,}\b")
_CODE_PREFIX = re.compile(r"(?:CE|CW|RE|NE)\d{4}\b")


def _read_directive(metadata: TestMetadata, directive: str, number: int,
                    test_file: Path) -> None:
    match = _DIRECTIVE_NAME.match(directive)
    if match is None or _CODE_PREFIX.match(directive):
        return
    name, rest = match.group(), directive[match.end():].lstrip()
    where = f"line {number} of the directive block"
    if name in FLAG_DIRECTIVES:
        flag = _flag_value(rest)
        if flag is None:
            metadata.directive_errors.append(
                f"{where}: {name} is a flag; write `{name}` or `{name}: true`, "
                f"not {directive!r}")
        else:
            setattr(metadata, FLAG_DIRECTIVES[name], flag)
    elif name in VALUED_DIRECTIVES:
        if rest.startswith(':'):
            VALUED_DIRECTIVES[name](metadata, rest[1:].strip(), test_file)
        else:
            metadata.directive_errors.append(
                f"{where}: {name} takes a value; write `{name}: <value>`, "
                f"not {directive!r}")
    else:
        metadata.directive_errors.append(
            f"{where}: {name} is not a directive the runner knows; reword the line "
            f"if it is prose")


def parse_test_metadata(test_file: Path) -> TestMetadata:
    """Parse test metadata from a Sushi source file."""
    metadata = TestMetadata()

    try:
        for number, line in enumerate(directive_block(test_file), start=1):
            line = line.strip()
            if line.startswith('#'):
                _read_directive(metadata, line[1:].strip(), number, test_file)

    except Exception as e:
        # A fixture whose directives cannot be read FAILS; it never passes unchecked.
        metadata.directive_errors.append(f"the directives cannot be read: {e}")

    _release_lib_flag(metadata, test_file)

    _apply_category_defaults(test_file, metadata)

    return metadata


def _apply_category_defaults(test_file: Path, metadata: TestMetadata) -> None:
    """Fill in the runtime contract implied by a test's filename category."""
    filename = test_file.name

    # test_err_* never produces a binary, so there is nothing to run.
    if filename.startswith('test_err_'):
        metadata.requires_runtime = False
        return

    # test_warn_* DOES produce a binary -- the warning exit code says nothing about
    # whether the program runs correctly. The category supplies a default, and an
    # explicit directive overrides it: otherwise a defect that only shows at runtime is
    # unassertable whenever the same program also warns, and "it warns" silently becomes
    # "its behaviour is nobody's business". Shadowing is the motivating case (CW1002 is
    # unavoidable in a program whose whole subject is a shadowed binding).
    if filename.startswith('test_warn_'):
        declares_runtime = (metadata.expect_runtime_exit is not None
                            or metadata.expect_stdout_exact is not None
                            or metadata.expect_stdout_contains is not None
                            or metadata.expect_no_leaks
                            or metadata.expect_no_open_fds)
        if not declares_runtime:
            metadata.requires_runtime = False
            return
        metadata.requires_runtime = True
        if metadata.expect_runtime_exit is None:
            metadata.expect_runtime_exit = 0
        return

    metadata.requires_runtime = True
    if metadata.expect_runtime_exit is None:
        metadata.expect_runtime_exit = 0


def get_test_category(test_file: Path) -> str:
    """Determine test category based on filename pattern."""
    filename = test_file.name

    if filename.startswith('test_err_'):
        return 'error'
    elif filename.startswith('test_warn_'):
        return 'warning'
    elif filename.startswith('test_run_'):
        return 'runtime'
    else:
        return 'success'


def should_run_runtime_test(test_file: Path, metadata: TestMetadata) -> bool:
    """Determine if a test should have its compiled binary executed."""
    category = get_test_category(test_file)

    if category == 'error':
        return False

    if category == 'warning':
        return metadata.expect_no_leaks or metadata.expect_no_open_fds

    return metadata.requires_runtime


# Directories the corpus glob steps over: `helpers` holds modules that are not
# standalone programs, `bin` holds what a run compiled, `v2` holds a rebuild fixture's
# second version.
EXCLUDED_FIXTURE_DIRS = {"helpers", "bin", REBUILD_DIR}


def collect_fixtures(tests_dir: Path) -> List[Path]:
    """Every `.sushi` fixture the harness runs, sorted.

    ONE collector, for both runners and for the gates that check the corpus. A gate
    that globbed on its own could pass over a fixture a runner runs.
    """
    tests_dir = Path(tests_dir)
    return [f for f in corpus_files(tests_dir, "test_*.sushi")
            if not (EXCLUDED_FIXTURE_DIRS & set(f.relative_to(tests_dir).parts))]


def corpus_files(root: Path, pattern: str = "*.sushi") -> List[Path]:
    """Every FILE under `root` whose name matches `pattern`, sorted.

    ONE reader of the `.sushi` corpus, for the collector and for every gate that scans
    the corpus. A directory whose name ends in `.sushi` (a fixture's unit that is a
    directory) is not a source and is never yielded.
    """
    return sorted(f for f in Path(root).rglob(pattern) if f.is_file())


# The code of a source the compiler cannot read. A fixture that declares it may keep
# files beside it that are not UTF-8.
UNREADABLE_SOURCE_CODE = "CE3017"


class CorpusReadError(ValueError):
    """A corpus file is not UTF-8 and no fixture beside it declares that it may be."""


def declares_unreadable_sources(directory: Path) -> bool:
    """A `test_err_` fixture in `directory` declares UNREADABLE_SOURCE_CODE in its directives."""
    for fixture in Path(directory).glob("test_err_*.sushi"):
        if not fixture.is_file():
            continue
        metadata = parse_test_metadata(fixture)
        codes = metadata.expect_error_code + (metadata.expect_error_codes_exact or [])
        if UNREADABLE_SOURCE_CODE in codes:
            return True
    return False


def corpus_text(path: Path) -> str:
    """The text of a corpus file, for a gate that scans the corpus.

    A UTF-8 file answers its whole text. A file that is not UTF-8 is a DECLARED
    NEGATIVE when a `test_err_` fixture in its own directory declares
    UNREADABLE_SOURCE_CODE; it answers its leading comment block alone, the one part
    of it that is text, and that block must be UTF-8. Any other file that is not UTF-8
    raises CorpusReadError: the rule is structural, and no file name is listed.
    """
    data = Path(path).read_bytes()
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as e:
        if not declares_unreadable_sources(Path(path).parent):
            raise CorpusReadError(
                f"{path}: byte 0x{data[e.start]:02x} at offset {e.start} is not valid "
                f"UTF-8, and no test_err_ fixture beside it declares "
                f"{UNREADABLE_SOURCE_CODE}") from None
    try:
        return "\n".join(directive_block(path))
    except DirectiveBlockError as e:
        raise CorpusReadError(f"{path}: {e}") from None


def select_fixtures(tests_dir: Path, *, filter_pattern: Optional[str] = None,
                    leaks_only: bool = False,
                    compile_only: bool = False) -> List[Path]:
    """The fixtures THIS run covers: `collect_fixtures`, narrowed by each flag.

    ONE selector. `collect_fixtures` answers what the corpus holds; this answers what a
    run was asked for, and every flag narrows the same list here rather than filtering a
    copy of its own somewhere in a runner.

    The flags compose, and a combination may legitimately select nothing -- a leak
    assertion needs a run, so `--leaks-only --compile-only` is empty. An empty selection
    is not this function's to refuse: it returns the empty list and the RUNNER fails the
    run, because a run that covered nothing must not report a pass (#765).
    """
    tests_dir = Path(tests_dir)
    selected = collect_fixtures(tests_dir)

    if filter_pattern:
        selected = [f for f in selected
                    if filter_pattern in str(f.relative_to(tests_dir))]

    if leaks_only:
        selected = [f for f in selected if parse_test_metadata(f).expect_no_leaks]

    # `--compile-only` is a SELECTOR over the rule that already decides whether a binary
    # runs, never a second rule and never a weaker check: what it selects is asserted in
    # full. The retired basic runner was the other shape -- the whole corpus with the
    # directives switched off -- and it bought no speed for it (#760).
    if compile_only:
        selected = [f for f in selected
                    if not should_run_runtime_test(f, parse_test_metadata(f))]

    return selected


def fixture_id(test_file: Path, tests_dir: Path) -> str:
    """A fixture's identity: its path under `tests/`, without the suffix.

    The file name alone is not an identity -- four stems named two fixtures each
    (#604). A fixture a gate builds in a temporary directory has no path under
    `tests/`; its own stem plus a digest of the directory holding it answers there,
    because two such fixtures may still carry one stem.
    """
    test_file = Path(test_file)
    try:
        relative = test_file.resolve().relative_to(Path(tests_dir).resolve())
    except ValueError:
        digest = hashlib.sha256(
            str(test_file.resolve().parent).encode("utf-8")).hexdigest()[:8]
        return f"{test_file.stem}-{digest}"
    return relative.with_suffix("").as_posix()


def fixture_binary_name(test_file: Path, tests_dir: Path) -> str:
    """The name of the binary a fixture compiles to: its identity, flattened.

    The runners compile into ONE directory, so the name has to carry the directory
    the fixture came from. It used to carry the process id instead, which every
    thread of a run shares -- so a shared stem was a shared binary.
    """
    return fixture_id(test_file, tests_dir).replace("/", "__")
