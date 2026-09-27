"""Test metadata parsing for the Sushi language test framework."""

import hashlib
import re
import sys
from dataclasses import dataclass
from typing import Optional, List, Dict
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
    # Sushi-source stdlib modules the compiler registers from the fixture's copy: name -> path.
    stdlib_modules: Optional[Dict[str, str]] = None

    # Test categorization
    test_type: str = "default"  # "default", "runtime", "compilation"

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
        if self.build_libs is None:
            self.build_libs = []
        if self.stdlib_modules is None:
            self.stdlib_modules = {}

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


def _compiler_flags(metadata: TestMetadata, value: str, test_file: Path) -> None:
    for token in _split(value):
        if token in RUNNER_OWNED_FLAGS:
            _warn(f"{token} is the runner's to spell in {test_file}; COMPILER_FLAGS ignored it")
            continue
        metadata.compiler_flags.append(token)


def _test_type(metadata: TestMetadata, value: str, test_file: Path) -> None:
    value = value.lower()
    if value in ('default', 'runtime', 'compilation', 'error', 'warning'):
        metadata.test_type = value
    else:
        _warn(f"Invalid TEST_TYPE value in {test_file}: {value}")


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
    'TEST_TYPE': _test_type,
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
    'BUILD_LIB': _extend('build_libs', lambda v: _split(_unquote(v))),
    'STDLIB_MODULE': _stdlib_module,
}

# Every directive that is a flag: bare `NAME` is true, `NAME: true|yes|1` sets it.
FLAG_DIRECTIVES = {
    'EXPECT_NO_LEAKS': 'expect_no_leaks',
    'EXPECT_NO_OPEN_FDS': 'expect_no_open_fds',
    'RUN_IN_FIXTURE_DIR': 'run_in_fixture_dir',
}

_DIRECTIVE_NAME = re.compile(r"[A-Z_]+")


def parse_test_metadata(test_file: Path) -> TestMetadata:
    """Parse test metadata from a Sushi source file."""
    metadata = TestMetadata()

    try:
        lines = test_file.read_text(encoding='utf-8').split('\n')
        for line in header_block(lines):
            line = line.strip()
            if not line.startswith('#'):
                continue
            directive = line[1:].strip()
            match = _DIRECTIVE_NAME.match(directive)
            if match is None:
                continue
            name, rest = match.group(), directive[match.end():]
            if name in FLAG_DIRECTIVES:
                flag = _flag_value(rest)
                if flag is not None:
                    setattr(metadata, FLAG_DIRECTIVES[name], flag)
            elif name in VALUED_DIRECTIVES and rest.startswith(':'):
                VALUED_DIRECTIVES[name](metadata, rest[1:].strip(), test_file)

    except Exception as e:
        _warn(f"Failed to parse metadata from {test_file}: {e}")

    _apply_category_defaults(test_file, metadata)

    return metadata


def _apply_category_defaults(test_file: Path, metadata: TestMetadata) -> None:
    """Fill in the runtime contract implied by a test's filename category."""
    filename = test_file.name

    # test_err_* never produces a binary, so there is nothing to run.
    if filename.startswith('test_err_'):
        metadata.test_type = 'compilation_only'
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
            metadata.test_type = 'compilation_only'
            metadata.requires_runtime = False
            return
        metadata.test_type = 'runtime'
        metadata.requires_runtime = True
        if metadata.expect_runtime_exit is None:
            metadata.expect_runtime_exit = 0
        return

    if filename.startswith('test_run_'):
        metadata.test_type = 'runtime'

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
    return sorted(
        f for f in tests_dir.rglob("test_*.sushi")
        if not (EXCLUDED_FIXTURE_DIRS & set(f.relative_to(tests_dir).parts))
    )


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
