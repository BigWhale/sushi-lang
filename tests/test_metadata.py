"""Test metadata parsing for the Sushi language test framework."""

import re
from dataclasses import dataclass
from typing import Optional, List, Dict
from pathlib import Path


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

    # Opt-in leak assertion, enforced by every enhanced run that executes the test.
    # --leaks-only narrows the selection to the tests carrying it; it does not decide
    # whether it is honoured.
    expect_no_leaks: bool = False

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


def header_block(lines: List[str]) -> List[str]:
    """The leading comment block: every line before the first line of CODE."""
    header = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            break
        header.append(line)
    return header


def parse_test_metadata(test_file: Path) -> TestMetadata:
    """Parse test metadata from a Sushi source file."""
    metadata = TestMetadata()

    try:
        content = test_file.read_text(encoding='utf-8')
        lines = content.split('\n')
        header_lines = header_block(lines)

        for line in header_lines:
            line = line.strip()
            if not line.startswith('#'):
                continue

            # Remove comment prefix and parse directive
            directive = line[1:].strip()

            if directive.startswith('EXPECT_RUNTIME_EXIT:'):
                value = directive.split(':', 1)[1].strip()
                try:
                    metadata.expect_runtime_exit = int(value)
                except ValueError:
                    print(f"Warning: Invalid EXPECT_RUNTIME_EXIT value in {test_file}: {value}")

            elif directive.startswith('EXPECT_STDOUT_CONTAINS:'):
                value = directive.split(':', 1)[1].strip()
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                # Handle escape sequences
                value = value.replace('\\n', '\n').replace('\\t', '\t')
                metadata.expect_stdout_contains.append(value)

            elif directive.startswith('EXPECT_STDOUT_EXACT:'):
                value = directive.split(':', 1)[1].strip()
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                # Handle escape sequences
                value = value.replace('\\n', '\n').replace('\\t', '\t')
                metadata.expect_stdout_exact = value

            elif directive.startswith('EXPECT_STDERR_CONTAINS:'):
                value = directive.split(':', 1)[1].strip()
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                value = value.replace('\\n', '\n').replace('\\t', '\t')
                metadata.expect_stderr_contains.append(value)

            elif directive.startswith('EXPECT_STDERR_EMPTY:'):
                value = directive.split(':', 1)[1].strip().lower()
                metadata.expect_stderr_empty = value in ('true', 'yes', '1')

            elif directive.startswith('EXPECT_NO_LEAKS'):
                rest = directive[len('EXPECT_NO_LEAKS'):].lstrip()
                if rest.startswith(':'):
                    value = rest[1:].strip().lower()
                    metadata.expect_no_leaks = value in ('true', 'yes', '1')
                elif rest == '':
                    # Bare `# EXPECT_NO_LEAKS` (no colon) means true.
                    metadata.expect_no_leaks = True

            elif directive.startswith('EXPECT_ERROR_CODE:'):
                value = directive.split(':', 1)[1].strip()
                # Strip optional quotes; accept a comma/space separated list and
                # allow the directive to be repeated for multi-error compiles.
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                for token in re.split(r'[,\s]+', value):
                    if token:
                        metadata.expect_error_code.append(token)

            elif directive.startswith('COMPILER_FLAGS:'):
                value = directive.split(':', 1)[1].strip()
                for token in re.split(r'[,\s]+', value):
                    if not token:
                        continue
                    if token in RUNNER_OWNED_FLAGS:
                        print(f"Warning: {token} is the runner's to spell in "
                              f"{test_file}; COMPILER_FLAGS ignored it")
                        continue
                    metadata.compiler_flags.append(token)

            elif directive.startswith('TIMEOUT_SECONDS:'):
                value = directive.split(':', 1)[1].strip()
                try:
                    metadata.timeout_seconds = int(value)
                except ValueError:
                    print(f"Warning: Invalid TIMEOUT_SECONDS value in {test_file}: {value}")

            elif directive.startswith('TEST_TYPE:'):
                value = directive.split(':', 1)[1].strip().lower()
                if value in ('default', 'runtime', 'compilation', 'error', 'warning'):
                    metadata.test_type = value
                else:
                    print(f"Warning: Invalid TEST_TYPE value in {test_file}: {value}")

            elif directive.startswith('CMD_ARGS:'):
                value = directive.split(':', 1)[1].strip()
                # Store the command-line arguments as-is (will be split by shell)
                metadata.cmd_args = value

            elif directive.startswith('STDIN_INPUT:'):
                value = directive.split(':', 1)[1].strip()
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                # Handle escape sequences
                value = value.replace('\\n', '\n').replace('\\t', '\t')
                metadata.stdin_input = value

            elif directive.startswith('TEST_ENV:'):
                value = directive.split(':', 1)[1].strip()
                # One KEY=VALUE per directive; the directive may be repeated to set
                # several variables. Lets a test pin HOME/USER/etc. instead of baking
                # the developer's host environment into an expected-stdout snapshot.
                if '=' in value:
                    key, val = value.split('=', 1)
                    metadata.test_env[key.strip()] = val.strip()
                else:
                    print(f"Warning: Invalid TEST_ENV value in {test_file}: {value}")

            elif directive.startswith('TEST_CWD:'):
                # Working directory to run the binary in, so getcwd()-style output is
                # host-independent (e.g. TEST_CWD: / yields a deterministic "/").
                metadata.test_cwd = directive.split(':', 1)[1].strip()

    except Exception as e:
        print(f"Warning: Failed to parse metadata from {test_file}: {e}")

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
                            or metadata.expect_no_leaks)
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
        return metadata.expect_no_leaks

    return metadata.requires_runtime
