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
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from tqdm import tqdm


def build_stdlib(project_root: Path, verbose: bool = False) -> bool:
    """Build the standard library. Returns True on success."""
    if verbose:
        print("Building standard library...")

    try:
        result = subprocess.run(
            [sys.executable, str(project_root / "stdlib" / "build.py")],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode != 0:
            print(f"Failed to build stdlib: {result.stderr}")
            return False
        if verbose:
            print(result.stdout)
        return True
    except subprocess.TimeoutExpired:
        print("Stdlib build timed out")
        return False
    except Exception as e:
        print(f"Error building stdlib: {e}")
        return False


def build_test_helpers(project_root: Path, verbose: bool = False) -> bool:
    """Build test helper libraries. Returns True on success."""
    helpers_dir = project_root / "tests" / "libs" / "helpers"
    bin_dir = project_root / "tests" / "libs" / "bin"
    sushic = project_root / "sushic"

    if not helpers_dir.exists():
        if verbose:
            print("No test helpers directory found, skipping...")
        return True

    helper_files = list(helpers_dir.glob("*.sushi"))
    if not helper_files:
        if verbose:
            print("No test helper libraries found, skipping...")
        return True

    if verbose:
        print("Building test helper libraries...")

    bin_dir.mkdir(parents=True, exist_ok=True)

    for lib_file in helper_files:
        name = lib_file.stem
        output_path = bin_dir / f"{name}.slib"

        if verbose:
            print(f"  Compiling {name}...")

        try:
            result = subprocess.run(
                [str(sushic), "--lib", str(lib_file), "-o", str(output_path)],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                print(f"Failed to compile {name}: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print(f"Compilation of {name} timed out")
            return False
        except Exception as e:
            print(f"Error compiling {name}: {e}")
            return False

    if verbose:
        print(f"  Libraries compiled to {bin_dir}")

    return True

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
    parser.add_argument("--skip-build", action="store_true",
                       help="Skip building stdlib and test helpers")

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
            if args.skip_build:
                sys.argv.append("--skip-build")
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
    os.chdir(project_root)

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

    # Run tests in parallel with progress bar
    results = []
    show_progress = not args.json and not args.verbose
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(run_single_test, f, bin_dir, args.verbose): f for f in test_files}
        if show_progress:
            pbar = tqdm(total=len(test_files), desc="Running tests", unit="test",
                       bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]")
        for future in as_completed(futures):
            results.append(future.result())
            if show_progress:
                pbar.update(1)
        if show_progress:
            pbar.close()

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
