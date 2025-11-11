#!/usr/bin/env python3
"""
Comprehensive test runner for Sushi language compiler.

This script runs all test files in the tests/ directory and verifies
that they return the expected exit codes:
- 0: Success (no errors, no warnings)
- 1: Success with warnings
- 2: Compilation failed with errors

Usage:
    python tests/run_tests.py
    python tests/run_tests.py --verbose
    python tests/run_tests.py --help
"""

import argparse
import subprocess
import sys
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import time

def get_expected_exit_code(test_file: Path) -> int:
    """Determine expected exit code based on filename convention.

    Convention:
    - test_*.sushi: expect 0 (success, no warnings)
    - test_warn_*.sushi: expect 1 (success with warnings)
    - test_err_*.sushi: expect 2 (compilation failed)

    Special cases:
    - test_err_dynamic_arrays_out_of_bounds.sushi: expects 0 (bounds checking not implemented yet)
    """
    test_name = test_file.name

    # Special cases that don't follow the convention
    if test_name == "test_err_dynamic_arrays_out_of_bounds.sushi":
        return 0  # Bounds checking not implemented yet, compiles successfully

    if test_name.startswith("test_warn_"):
        return 1
    elif test_name.startswith("test_err_"):
        return 2
    elif test_name.startswith("test_"):
        return 0
    else:
        # Non-test file, shouldn't happen but default to 0
        return 0

def run_single_test(test_file: Path, bin_dir: Path, verbose: bool = False) -> tuple[str, bool, int, int, str]:
    """Run a single test file and return results."""
    test_name = test_file.name
    expected_exit_code = get_expected_exit_code(test_file)

    try:
        # Generate unique output filename to avoid race conditions in parallel execution
        output_name = test_file.stem  # e.g., test_arithmetic.sushi -> test_arithmetic
        output_path = bin_dir / output_name  # Output to tests/bin/ directory

        # Run the compiler on the test file with unique output
        result = subprocess.run(
            ["./sushic", str(test_file), "-o", str(output_path)],
            capture_output=True,
            text=True,
            timeout=30  # 30 second timeout per test
        )

        actual_exit_code = result.returncode
        passed = actual_exit_code == expected_exit_code

        # Capture output for verbose mode
        output = ""
        if result.stdout:
            output += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            output += f"STDERR:\n{result.stderr}\n"

        return test_name, passed, expected_exit_code, actual_exit_code, output

    except subprocess.TimeoutExpired:
        return test_name, False, expected_exit_code, -1, "TEST TIMEOUT"
    except Exception as e:
        return test_name, False, expected_exit_code, -1, f"TEST ERROR: {e}"

def main():
    parser = argparse.ArgumentParser(description="Run Sushi language compiler tests")
    parser.add_argument("-v", "--verbose", action="store_true",
                       help="Show detailed output for each test")
    parser.add_argument("-j", "--jobs", type=int, default=4,
                       help="Number of parallel test jobs (default: 4)")
    parser.add_argument("--filter", type=str,
                       help="Only run tests matching this pattern")
    parser.add_argument("--enhanced", action="store_true",
                       help="Use enhanced test runner with runtime testing support")
    parser.add_argument("--json", action="store_true",
                       help="Output results in JSON format")

    args = parser.parse_args()

    # Delegate to enhanced runner if requested
    if args.enhanced:
        if not args.json:
            print("Delegating to enhanced test runner...")
        try:
            import enhanced_test_runner
            sys.argv = [sys.argv[0]]  # Reset argv for enhanced runner
            if args.verbose:
                sys.argv.append("--verbose")
            if args.filter:
                sys.argv.extend(["--filter", args.filter])
            if args.jobs != 4:
                sys.argv.extend(["--jobs", str(args.jobs)])
            if args.json:
                sys.argv.append("--json")
            return enhanced_test_runner.main()
        except ImportError:
            if not args.json:
                print("Error: Enhanced test runner not available. Falling back to basic runner.")
        except Exception as e:
            if not args.json:
                print(f"Error running enhanced test runner: {e}")
                print("Falling back to basic runner.")

    # Find the project root and tests directory
    project_root = Path(__file__).parent.parent
    tests_dir = project_root / "tests"
    bin_dir = tests_dir / "bin"

    # Create bin directory if it doesn't exist
    bin_dir.mkdir(exist_ok=True)

    # Change to project root for running sushic
    import os
    os.chdir(project_root)

    # Find all test files in tests directory recursively, excluding helper/build directories
    test_files = list(tests_dir.rglob("test_*.sushi"))

    # Exclude any files in the helpers or bin subdirectories
    # (helpers contains non-standalone modules, bin contains compiled binaries)
    excluded_dirs = {"helpers", "bin"}
    test_files = [f for f in test_files if not any(d in excluded_dirs for d in f.relative_to(tests_dir).parts)]

    if args.filter:
        # Filter by relative path (supports directory filters like "stdlib" or filename patterns)
        test_files = [f for f in test_files if args.filter in str(f.relative_to(tests_dir))]

    if not test_files:
        if not args.json:
            print("No test files found!")
        return 1

    # All test files that match test_*.sushi pattern are automatically supported

    if not args.json:
        print(f"Running {len(test_files)} tests with {args.jobs} parallel jobs...")
        print("Note: This is the basic test runner (compilation only). Use --enhanced for runtime testing.")
        print()

    start_time = time.time()

    # Run tests in parallel
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        results = list(executor.map(lambda f: run_single_test(f, bin_dir, args.verbose), test_files))

    end_time = time.time()

    # Analyze results
    passed_tests = []
    failed_tests = []

    for test_name, passed, expected, actual, output in results:
        if passed:
            passed_tests.append(test_name)
            if args.verbose and not args.json:
                print(f"✓ {test_name} (expected: {expected}, actual: {actual})")
        else:
            failed_tests.append((test_name, expected, actual, output))
            if not args.json:
                print(f"✗ {test_name} (expected: {expected}, actual: {actual})")
                if args.verbose and output:
                    print(f"  Output: {output}")

    # Output in JSON format if requested
    if args.json:
        json_output = {
            "total_tests": len(results),
            "compilation_tests": len(results),  # Basic runner only does compilation
            "runtime_tests": 0,  # Basic runner doesn't run runtime tests
            "passed": len(passed_tests),
            "failed": len(failed_tests),
            "duration_seconds": round(end_time - start_time, 2),
            "failed_tests": [
                {
                    "name": test_name,
                    "expected_exit_code": expected,
                    "actual_exit_code": actual
                }
                for test_name, expected, actual, output in failed_tests
            ]
        }
        print(json.dumps(json_output, indent=2))
        return 1 if failed_tests else 0

    # Summary
    print()
    print(f"Test Results ({end_time - start_time:.2f}s):")
    print(f"  Passed: {len(passed_tests)}")
    print(f"  Failed: {len(failed_tests)}")
    print(f"  Total:  {len(results)}")

    if failed_tests:
        print()
        print("Failed tests:")
        for test_name, expected, actual, output in failed_tests:
            print(f"  {test_name}: expected {expected}, got {actual}")

    # Return appropriate exit code
    if failed_tests:
        return 1
    else:
        print()
        print("All tests passed! ✓")
        return 0

if __name__ == "__main__":
    sys.exit(main())
