"""
Test metadata parsing for the Sushi language test framework.

Provides functionality to parse test metadata from Sushi source files
to specify expected runtime behavior, exit codes, and output validation.
"""

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

    # Opt-in leak assertion. Only honoured when the runner is invoked with --leaks;
    # the default suite never pays the cost of a leak checker.
    expect_no_leaks: bool = False

    # Test behavior flags
    requires_runtime: bool = False
    timeout_seconds: int = 10
    cmd_args: Optional[str] = None  # Command-line arguments for runtime test
    stdin_input: Optional[str] = None  # Standard input to provide to the test
    test_env: Optional[Dict[str, str]] = None  # Env vars to set for the runtime binary
    test_cwd: Optional[str] = None  # Working directory to run the runtime binary in

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

        # If any runtime expectations are set, this test requires runtime validation
        if (self.expect_runtime_exit is not None or
            self.expect_stdout_contains or
            self.expect_stdout_exact is not None or
            self.expect_stderr_contains or
            self.expect_stderr_empty):
            self.requires_runtime = True


def parse_test_metadata(test_file: Path) -> TestMetadata:
    """
    Parse test metadata from a Sushi source file.

    Looks for special comments at the top of the file:
    # EXPECT_RUNTIME_EXIT: 42
    # EXPECT_STDOUT_CONTAINS: "Result: 17"
    # EXPECT_STDOUT_EXACT: "Hello World\\nDone\\n"
    # EXPECT_STDERR_EMPTY: true
    # EXPECT_NO_LEAKS: true
    # EXPECT_ERROR_CODE: CE2007
    # TIMEOUT_SECONDS: 10
    # TEST_TYPE: runtime
    # CMD_ARGS: arg1 arg2 arg3
    # STDIN_INPUT: "line1\\nline2\\nline3\\n"
    # TEST_ENV: HOME=/home/trillian     (repeatable, one KEY=VALUE per line)
    # TEST_CWD: /

    Args:
        test_file: Path to the .sushi test file

    Returns:
        TestMetadata object with parsed expectations
    """
    metadata = TestMetadata()

    try:
        content = test_file.read_text(encoding='utf-8')
        lines = content.split('\n')

        # Only parse metadata from the first 20 lines (before main logic)
        header_lines = lines[:20]

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

            elif directive.startswith('EXPECT_NO_LEAKS:'):
                value = directive.split(':', 1)[1].strip().lower()
                metadata.expect_no_leaks = value in ('true', 'yes', '1')

            elif directive.startswith('EXPECT_ERROR_CODE:'):
                value = directive.split(':', 1)[1].strip()
                # Strip optional quotes; accept a comma/space separated list and
                # allow the directive to be repeated for multi-error compiles.
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                for token in re.split(r'[,\s]+', value):
                    if token:
                        metadata.expect_error_code.append(token)

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
    """
    Fill in the runtime contract implied by a test's filename category.

    A runnable test (anything that is not test_err_* / test_warn_*) is executed after
    compilation and is expected to exit 0 unless it declares otherwise. Making that
    default explicit here -- rather than inferring intent from the source text -- is
    what lets the runner treat an undeclared non-zero exit as a failure.

    Args:
        test_file: Path to the test file
        metadata: TestMetadata object to update
    """
    filename = test_file.name

    if filename.startswith('test_err_') or filename.startswith('test_warn_'):
        metadata.test_type = 'compilation_only'
        metadata.requires_runtime = False
        return

    if filename.startswith('test_run_'):
        metadata.test_type = 'runtime'

    metadata.requires_runtime = True
    if metadata.expect_runtime_exit is None:
        metadata.expect_runtime_exit = 0


def get_test_category(test_file: Path) -> str:
    """
    Determine test category based on filename pattern.

    Returns:
        'error': Should fail compilation (test_err_*)
        'warning': Should succeed with warnings (test_warn_*)
        'success': Should succeed without warnings (test_*)
        'runtime': Should succeed and be executed (test_run_*)
    """
    filename = test_file.name

    if filename.startswith('test_err_'):
        return 'error'
    elif filename.startswith('test_warn_'):
        return 'warning'
    elif filename.startswith('test_run_'):
        return 'runtime'
    else:
        return 'success'


def should_run_runtime_test(test_file: Path, metadata: TestMetadata,
                            leaks_mode: bool = False) -> bool:
    """
    Determine if a test should have its compiled binary executed.

    Reduces to "run everything that is not test_err_ / test_warn_", because
    _apply_category_defaults marks every runnable test as requires_runtime.

    The one exception is leaks_mode: a test_warn_ test compiles successfully and does
    produce a binary, so a warning test that declares EXPECT_NO_LEAKS is executed
    under --leaks. Shadowing is a warned-but-legal construct, so this is the only way
    to leak-check it.

    Args:
        test_file: Path to the test file
        metadata: Parsed test metadata
        leaks_mode: True when the runner was invoked with --leaks

    Returns:
        True if the test should be executed after compilation
    """
    category = get_test_category(test_file)

    if category == 'error':
        return False

    if category == 'warning':
        return leaks_mode and metadata.expect_no_leaks

    return metadata.requires_runtime
