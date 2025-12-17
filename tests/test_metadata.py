"""
Test metadata parsing for the Sushi language test framework.

Provides functionality to parse test metadata from Sushi source files
to specify expected runtime behavior, exit codes, and output validation.
"""

import re
from dataclasses import dataclass
from typing import Optional, List
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

    # Test behavior flags
    requires_runtime: bool = False
    timeout_seconds: int = 10
    cmd_args: Optional[str] = None  # Command-line arguments for runtime test
    stdin_input: Optional[str] = None  # Standard input to provide to the test

    # Test categorization
    test_type: str = "default"  # "default", "runtime", "compilation"

    def __post_init__(self):
        """Post-initialization processing."""
        if self.expect_stdout_contains is None:
            self.expect_stdout_contains = []
        if self.expect_stderr_contains is None:
            self.expect_stderr_contains = []

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
    # TIMEOUT_SECONDS: 10
    # TEST_TYPE: runtime
    # CMD_ARGS: arg1 arg2 arg3
    # STDIN_INPUT: "line1\\nline2\\nline3\\n"

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

    except Exception as e:
        print(f"Warning: Failed to parse metadata from {test_file}: {e}")

    # Auto-detect runtime requirements for known test patterns
    _auto_detect_runtime_requirements(test_file, metadata, content if 'content' in locals() else '')

    return metadata


def _auto_detect_runtime_requirements(test_file: Path, metadata: TestMetadata, content: str) -> None:
    """
    Auto-detect if a test requires runtime validation based on filename and content patterns.

    Args:
        test_file: Path to the test file
        metadata: TestMetadata object to update
        content: Source file content
    """
    filename = test_file.name

    # Auto-detect based on filename patterns
    if filename.startswith('test_run_'):
        metadata.requires_runtime = True
        metadata.test_type = 'runtime'
    elif filename.startswith('test_err_') or filename.startswith('test_warn_'):
        metadata.test_type = 'compilation_only'
        metadata.requires_runtime = False
        return

    # Auto-detect based on content patterns
    if not metadata.requires_runtime:  # Only auto-detect if not explicitly set

        # Look for conditional return statements (runtime validation logic)
        conditional_return_patterns = [
            r'if\s*\([^)]+\):\s*return\s+[0-9]+',  # if (condition): return N
            r'return\s+[^0\s]\d*',  # return non-zero number
        ]

        for pattern in conditional_return_patterns:
            if re.search(pattern, content):
                metadata.requires_runtime = True
                if metadata.expect_runtime_exit is None:
                    # Try to extract expected success exit code (usually 0)
                    success_match = re.search(r'return\s+0\s*#.*[Ss]uccess', content)
                    if success_match:
                        metadata.expect_runtime_exit = 0
                break

        # Look for specific expected values in comments
        expected_patterns = [
            r'#.*should be.*(\d+)',
            r'#.*expected.*(\d+)',
            r'#.*result.*(\d+)',
        ]

        for pattern in expected_patterns:
            if re.search(pattern, content):
                metadata.requires_runtime = True
                break


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


def should_run_runtime_test(test_file: Path, metadata: TestMetadata) -> bool:
    """
    Determine if a test should have its compiled binary executed.

    Args:
        test_file: Path to the test file
        metadata: Parsed test metadata

    Returns:
        True if the test should be executed after compilation
    """
    category = get_test_category(test_file)

    # Never run runtime tests for compilation-only categories
    if category in ('error', 'warning'):
        return False

    # Always run for explicit runtime tests
    if category == 'runtime':
        return True

    # For regular tests, check metadata requirements
    return metadata.requires_runtime
