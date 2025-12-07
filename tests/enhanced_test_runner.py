#!/usr/bin/env python3
"""
Enhanced test runner for the Sushi language compiler.

Provides two-phase testing:
1. Compilation phase: Validates compilation success/failure/warnings
2. Runtime phase: Executes compiled binaries and validates runtime behavior

Supports test metadata for specifying expected runtime behavior.
"""

import subprocess
import sys
import tempfile
import shutil
import argparse
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import time
import os

from test_metadata import parse_test_metadata, get_test_category, should_run_runtime_test, TestMetadata
from run_tests import build_stdlib, build_test_helpers


@dataclass
class TestResult:
    """Result of running a single test."""
    name: str
    category: str
    compilation_success: bool
    compilation_message: str
    runtime_success: Optional[bool] = None
    runtime_message: Optional[str] = None
    skipped_runtime: bool = False
    total_success: bool = False

    def __post_init__(self):
        """Calculate overall success after initialization."""
        if self.skipped_runtime:
            self.total_success = self.compilation_success
        else:
            self.total_success = self.compilation_success and (self.runtime_success is not False)


class TestRunner:
    """Enhanced test runner with compilation and runtime testing."""

    def __init__(self, tests_dir: Path, mode: str = "full", verbose: bool = False, parallel_jobs: int = 4, json_output: bool = False):
        """
        Initialize the test runner.

        Args:
            tests_dir: Directory containing test files
            mode: Testing mode ("compile", "runtime", "full")
            verbose: Enable verbose output
            parallel_jobs: Number of parallel test jobs
            json_output: Output results in JSON format
        """
        self.tests_dir = tests_dir
        self.mode = mode
        self.verbose = verbose
        self.parallel_jobs = parallel_jobs
        self.json_output = json_output
        self.temp_dir = None

    def __enter__(self):
        """Create temporary directory for test binaries."""
        self.temp_dir = tempfile.mkdtemp(prefix="sushi_tests_")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up temporary directory."""
        if self.temp_dir and Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def run_all_tests(self, filter_pattern: str = None) -> Dict[str, TestResult]:
        """
        Run all tests in the test directory.

        Args:
            filter_pattern: Optional pattern to filter tests by path

        Returns:
            Dictionary mapping test names to TestResult objects
        """
        test_files = sorted(self.tests_dir.rglob("test_*.sushi"))

        # Exclude files in helpers or bin subdirectories
        # (helpers contains non-standalone modules, bin contains compiled binaries)
        excluded_dirs = {"helpers", "bin"}
        test_files = [f for f in test_files if not any(d in excluded_dirs for d in f.relative_to(self.tests_dir).parts)]

        # Filter by relative path if pattern provided
        if filter_pattern:
            test_files = [f for f in test_files if filter_pattern in str(f.relative_to(self.tests_dir))]

        if not test_files:
            if not self.json_output:
                print("No test files found!")
            return {}

        if not self.json_output:
            print(f"Running {len(test_files)} tests with {self.parallel_jobs} parallel jobs...")
            if self.mode != "compile":
                print(f"Using temporary directory: {self.temp_dir}")
            print()

        start_time = time.time()

        # Run tests in parallel
        results = {}
        with ThreadPoolExecutor(max_workers=self.parallel_jobs) as executor:
            # Submit all test jobs
            future_to_test = {executor.submit(self.run_single_test, test_file): test_file.name
                             for test_file in test_files}

            # Collect results as they complete
            for future in as_completed(future_to_test):
                test_name = future_to_test[future]
                try:
                    result = future.result()
                    results[test_name] = result
                    if not self.json_output and (self.verbose or not result.total_success):
                        self._print_test_result(result)
                except Exception as e:
                    if not self.json_output:
                        print(f"ERROR: Test {test_name} crashed: {e}")
                    results[test_name] = TestResult(
                        name=test_name,
                        category="error",
                        compilation_success=False,
                        compilation_message=f"Test runner exception: {e}",
                        skipped_runtime=True
                    )

        end_time = time.time()

        self._print_summary(results, end_time - start_time)
        return results

    def run_single_test(self, test_file: Path) -> TestResult:
        """
        Run a single test file through compilation and optionally runtime phases.

        Args:
            test_file: Path to the .sushi test file

        Returns:
            TestResult object with compilation and runtime results
        """
        test_name = test_file.name
        category = get_test_category(test_file)
        metadata = parse_test_metadata(test_file)

        # Phase 1: Compilation
        compilation_success, compilation_message = self._run_compilation_test(test_file, category)

        result = TestResult(
            name=test_name,
            category=category,
            compilation_success=compilation_success,
            compilation_message=compilation_message
        )

        # Phase 2: Runtime (if applicable and requested)
        if (self.mode in ("runtime", "full") and
            compilation_success and
            should_run_runtime_test(test_file, metadata)):

            runtime_success, runtime_message = self._run_runtime_test(test_file, metadata)
            result.runtime_success = runtime_success
            result.runtime_message = runtime_message
            # Recalculate total success after runtime test
            result.total_success = result.compilation_success and result.runtime_success
        else:
            result.skipped_runtime = True
            result.total_success = result.compilation_success

        return result

    def _run_compilation_test(self, test_file: Path, category: str) -> Tuple[bool, str]:
        """
        Run compilation phase for a test.

        Args:
            test_file: Path to the test file
            category: Test category (error/warning/success/runtime)

        Returns:
            (success, message) tuple
        """
        # Determine expected exit code based on category
        expected_exit_codes = {
            'error': 2,      # Should fail compilation
            'warning': 1,    # Should succeed with warnings
            'success': 0,    # Should succeed without warnings
            'runtime': 0,    # Should succeed without warnings
        }
        expected_exit_code = expected_exit_codes.get(category, 0)

        try:
            # Special case for dynamic array out of bounds test
            if test_file.name == "test_err_dynamic_arrays_out_of_bounds.sushi":
                expected_exit_code = 0  # Should compile successfully

            # Create unique output binary name
            binary_name = f"test_{test_file.stem}_{os.getpid()}"
            binary_path = Path(self.temp_dir) / binary_name

            # Run the compiler (from project root)
            project_root = self.tests_dir.parent
            cmd = ["./sushic", str(test_file), "-o", str(binary_path)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=project_root,
                timeout=30  # 30 second timeout for compilation
            )

            success = result.returncode == expected_exit_code
            if success:
                message = f"✓ Compilation: Expected exit code {expected_exit_code}"
            else:
                message = f"✗ Compilation: Expected {expected_exit_code}, got {result.returncode}"
                if result.stderr:
                    message += f"\nSTDERR: {result.stderr.strip()}"
                if result.stdout:
                    message += f"\nSTDOUT: {result.stdout.strip()}"

            return success, message

        except subprocess.TimeoutExpired:
            return False, "✗ Compilation: Timeout (30s)"
        except Exception as e:
            return False, f"✗ Compilation: Exception: {e}"

    def _run_runtime_test(self, test_file: Path, metadata: TestMetadata) -> Tuple[bool, str]:
        """
        Run runtime phase for a test.

        Args:
            test_file: Path to the test file
            metadata: Test metadata with runtime expectations

        Returns:
            (success, message) tuple
        """
        try:
            # Find the compiled binary
            binary_name = f"test_{test_file.stem}_{os.getpid()}"
            binary_path = Path(self.temp_dir) / binary_name

            if not binary_path.exists():
                return False, "✗ Runtime: Binary not found after compilation"

            # Make binary executable
            binary_path.chmod(0o755)

            # Prepare command with arguments if specified
            cmd = [str(binary_path)]
            if metadata.cmd_args:
                # Split command-line arguments by whitespace (simple splitting)
                cmd.extend(metadata.cmd_args.split())

            # Prepare stdin input if specified
            stdin_input = metadata.stdin_input if metadata.stdin_input else None

            # Execute the binary
            result = subprocess.run(
                cmd,
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=metadata.timeout_seconds
            )

            # Validate runtime behavior
            success, message = self._validate_runtime_result(result, metadata)

            # Clean up binary after execution
            try:
                binary_path.unlink()
            except OSError:
                pass  # Ignore cleanup errors

            return success, message

        except subprocess.TimeoutExpired:
            return False, f"✗ Runtime: Timeout ({metadata.timeout_seconds}s)"
        except Exception as e:
            return False, f"✗ Runtime: Exception: {e}"

    def _validate_runtime_result(self, result: subprocess.CompletedProcess, metadata: TestMetadata) -> Tuple[bool, str]:
        """
        Validate runtime execution result against metadata expectations.

        Args:
            result: subprocess result from binary execution
            metadata: Test metadata with expectations

        Returns:
            (success, message) tuple
        """
        messages = []
        success = True

        # Check exit code
        if metadata.expect_runtime_exit is not None:
            if result.returncode == metadata.expect_runtime_exit:
                messages.append(f"✓ Exit code: {result.returncode}")
            else:
                messages.append(f"✗ Exit code: Expected {metadata.expect_runtime_exit}, got {result.returncode}")
                success = False
        else:
            # Default expectation: exit code 0 for success
            if result.returncode == 0:
                messages.append(f"✓ Exit code: {result.returncode}")
            else:
                messages.append(f"⚠ Exit code: {result.returncode} (no expectation set)")

        # Check stdout content
        if metadata.expect_stdout_exact is not None:
            if result.stdout == metadata.expect_stdout_exact:
                messages.append("✓ Stdout matches expected")
            else:
                messages.append(f"✗ Stdout mismatch\nExpected: {repr(metadata.expect_stdout_exact)}\nActual: {repr(result.stdout)}")
                success = False

        for expected_content in metadata.expect_stdout_contains:
            if expected_content in result.stdout:
                messages.append(f"✓ Stdout contains: {repr(expected_content)}")
            else:
                messages.append(f"✗ Stdout missing: {repr(expected_content)}\nActual stdout: {repr(result.stdout)}")
                success = False

        # Check stderr content
        if metadata.expect_stderr_empty and result.stderr:
            messages.append(f"✗ Expected empty stderr, got: {repr(result.stderr)}")
            success = False
        elif metadata.expect_stderr_empty:
            messages.append("✓ Stderr is empty")

        for expected_content in metadata.expect_stderr_contains:
            if expected_content in result.stderr:
                messages.append(f"✓ Stderr contains: {repr(expected_content)}")
            else:
                messages.append(f"✗ Stderr missing: {repr(expected_content)}\nActual stderr: {repr(result.stderr)}")
                success = False

        if success:
            summary = "✓ Runtime: All validations passed"
        else:
            summary = "✗ Runtime: Validation failed"

        full_message = summary + "\n" + "\n".join(f"  {msg}" for msg in messages)
        return success, full_message

    def _print_test_result(self, result: TestResult) -> None:
        """Print result for a single test."""
        status = "PASS" if result.total_success else "FAIL"
        print(f"[{status}] {result.name}")

        if not result.compilation_success or self.verbose:
            print(f"  Compilation: {result.compilation_message}")

        if not result.skipped_runtime:
            if not result.runtime_success or self.verbose:
                print(f"  Runtime: {result.runtime_message}")
        elif self.verbose:
            print(f"  Runtime: Skipped")

    def _print_summary(self, results: Dict[str, TestResult], duration: float) -> None:
        """Print test summary."""
        total_tests = len(results)
        passed_tests = sum(1 for r in results.values() if r.total_success)
        failed_tests = total_tests - passed_tests

        compilation_tests = sum(1 for r in results.values() if not r.skipped_runtime or not r.compilation_success)
        runtime_tests = sum(1 for r in results.values() if not r.skipped_runtime)

        if self.json_output:
            # Build list of failed tests with details
            failed_test_list = []
            for name, result in results.items():
                if not result.total_success:
                    failed_info = {
                        "name": name,
                        "category": result.category
                    }
                    if not result.compilation_success:
                        failed_info["failure_type"] = "compilation"
                        failed_info["message"] = result.compilation_message
                    elif not result.runtime_success:
                        failed_info["failure_type"] = "runtime"
                        failed_info["message"] = result.runtime_message
                    failed_test_list.append(failed_info)

            json_output = {
                "total_tests": total_tests,
                "compilation_tests": compilation_tests,
                "runtime_tests": runtime_tests,
                "passed": passed_tests,
                "failed": failed_tests,
                "duration_seconds": round(duration, 2),
                "failed_tests": failed_test_list
            }
            print(json.dumps(json_output, indent=2))
        else:
            print(f"\nTest Results ({duration:.2f}s):")
            print(f"  Total tests: {total_tests}")
            print(f"  Compilation tests: {compilation_tests}")
            print(f"  Runtime tests: {runtime_tests}")
            print(f"  Passed: {passed_tests}")
            print(f"  Failed: {failed_tests}")

            if failed_tests == 0:
                print("\nAll tests passed! ✓")
            else:
                print(f"\n{failed_tests} test(s) failed! ✗")

                # Show failed test details
                print("\nFailed tests:")
                for name, result in results.items():
                    if not result.total_success:
                        print(f"  {name}: ", end="")
                        if not result.compilation_success:
                            print("Compilation failed")
                        elif not result.runtime_success:
                            print("Runtime failed")


def main():
    """Main entry point for the enhanced test runner."""
    parser = argparse.ArgumentParser(description="Enhanced Sushi language test runner")

    parser.add_argument(
        "--mode",
        choices=["compile", "runtime", "full"],
        default="full",
        help="Testing mode: compile-only, runtime-only, or full (default: full)"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    parser.add_argument(
        "--jobs", "-j",
        type=int,
        default=4,
        help="Number of parallel test jobs (default: 4)"
    )

    parser.add_argument(
        "--filter",
        help="Run only tests matching this pattern"
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format"
    )

    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip building stdlib and test helpers"
    )

    args = parser.parse_args()

    tests_dir = Path(__file__).parent
    project_root = tests_dir.parent

    # Build stdlib and test helpers unless skipped
    if not args.skip_build:
        if not args.json:
            print("Building stdlib and test helpers...")
        if not build_stdlib(project_root, args.verbose):
            if not args.json:
                print("Failed to build stdlib, aborting tests")
            return 1
        if not build_test_helpers(project_root, args.verbose):
            if not args.json:
                print("Failed to build test helpers, aborting tests")
            return 1

    # Set SUSHI_LIB_PATH for library tests
    libs_bin_dir = tests_dir / "libs" / "bin"
    os.environ["SUSHI_LIB_PATH"] = str(libs_bin_dir)

    with TestRunner(tests_dir, args.mode, args.verbose, args.jobs, args.json) as runner:
        results = runner.run_all_tests(filter_pattern=args.filter)

    # Exit with appropriate code
    failed_count = sum(1 for r in results.values() if not r.total_success)
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())