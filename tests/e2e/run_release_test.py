#!/usr/bin/env python3
"""End-to-end release test for sushi-lang wheels.

Installs a wheel into a clean venv and compiles+runs test programs
to verify packaging correctness (entry points, stdlib bundling, linking).

Usage:
    python tests/e2e/run_release_test.py --wheel dist/sushi_lang-0.6.0-py3-none-any.whl
    python tests/e2e/run_release_test.py --from-release
    python tests/e2e/run_release_test.py --from-release v0.6.0
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROGRAMS_DIR = Path(__file__).parent / "programs"

TESTS = [
    {
        "name": "01_minimal",
        "file": "01_minimal.sushi",
        "expected_stdout": [],
        "exit_code": 0,
    },
    {
        "name": "02_strings",
        "file": "02_strings.sushi",
        "expected_stdout": ["15", "MOSTLY HARMLESS", "PASS"],
        "exit_code": 0,
    },
    {
        "name": "03_stdio",
        "file": "03_stdio.sushi",
        "expected_stdout": ["Mostly Harmless"],
        "exit_code": 0,
    },
    {
        "name": "04_hashmap",
        "file": "04_hashmap.sushi",
        "expected_stdout": ["PASS"],
        "exit_code": 0,
    },
    {
        "name": "05_multifile",
        "file": "05_multifile.sushi",
        "expected_stdout": ["PASS"],
        "exit_code": 0,
    },
]


def download_release_wheel(tag, dest_dir):
    """Download wheel from GitHub release using gh CLI."""
    cmd = ["gh", "release", "download", "--pattern", "*.whl", "--dir", dest_dir]
    if tag:
        cmd.insert(3, tag)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Failed to download release: {result.stderr}")
        sys.exit(1)
    wheels = list(Path(dest_dir).glob("*.whl"))
    if not wheels:
        print("No wheel found in release assets")
        sys.exit(1)
    return wheels[0]


def has_uv():
    """Check if uv is available."""
    return shutil.which("uv") is not None


def create_venv(python_exe, venv_dir):
    """Create a clean virtual environment."""
    if has_uv():
        subprocess.run(
            ["uv", "venv", "--python", python_exe, str(venv_dir)],
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            [python_exe, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
        )
    bin_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
    return bin_dir


def install_wheel(venv_dir, bin_dir, wheel_path):
    """Install wheel into the venv."""
    if has_uv():
        result = subprocess.run(
            ["uv", "pip", "install", "--python", str(bin_dir / "python"), str(wheel_path)],
            capture_output=True,
            text=True,
        )
    else:
        result = subprocess.run(
            [str(bin_dir / "pip"), "install", str(wheel_path)],
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        print(f"Failed to install wheel: {result.stderr}")
        sys.exit(1)


def run_test(sushic, work_dir, test):
    """Compile and run a single test program. Returns (passed, detail)."""
    source = work_dir / test["file"]
    binary = work_dir / test["name"]

    # Compile
    compile_result = subprocess.run(
        [str(sushic), str(source), "-o", str(binary)],
        capture_output=True,
        text=True,
        cwd=str(work_dir),
    )
    if compile_result.returncode != 0:
        detail = f"Compilation failed (exit {compile_result.returncode})"
        if compile_result.stderr:
            detail += f"\nstderr: {compile_result.stderr.strip()}"
        if compile_result.stdout:
            detail += f"\nstdout: {compile_result.stdout.strip()}"
        return False, detail

    if not binary.exists():
        return False, "Binary not produced"

    # Run
    run_result = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if run_result.returncode != test["exit_code"]:
        return False, (
            f"Exit code {run_result.returncode}, expected {test['exit_code']}"
            f"\nstdout: {run_result.stdout.strip()}"
        )

    # Check expected stdout patterns
    stdout = run_result.stdout
    for pattern in test["expected_stdout"]:
        if pattern not in stdout:
            return False, f"Missing expected output: {pattern!r}\nGot: {stdout.strip()}"

    return True, "OK"


def main():
    parser = argparse.ArgumentParser(description="E2E release test for sushi-lang")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--wheel", type=str, help="Path to wheel file")
    group.add_argument(
        "--from-release",
        nargs="?",
        const="",
        metavar="TAG",
        help="Download wheel from GitHub release (optionally specify tag)",
    )
    parser.add_argument(
        "--python",
        type=str,
        default=None,
        help="Python executable for venv (default: sys.executable)",
    )
    args = parser.parse_args()

    python_exe = args.python or sys.executable

    tmp_dirs = []

    try:
        # Resolve wheel path
        if args.from_release is not None:
            dl_dir = tempfile.mkdtemp(prefix="sushi-e2e-dl-")
            tmp_dirs.append(dl_dir)
            tag = args.from_release if args.from_release else None
            wheel_path = download_release_wheel(tag, dl_dir)
            print(f"Downloaded: {wheel_path.name}")
        else:
            wheel_path = Path(args.wheel).resolve()
            if not wheel_path.exists():
                print(f"Wheel not found: {wheel_path}")
                sys.exit(1)

        # Create venv and install
        venv_dir = Path(tempfile.mkdtemp(prefix="sushi-e2e-venv-"))
        tmp_dirs.append(str(venv_dir))
        print(f"Creating venv: {venv_dir} (python: {python_exe})")
        bin_dir = create_venv(python_exe, venv_dir)

        print(f"Installing: {wheel_path.name}")
        install_wheel(venv_dir, bin_dir, wheel_path)

        # Verify sushic exists
        sushic = bin_dir / "sushic"
        if not sushic.exists():
            print(f"sushic not found in {bin_dir}")
            sys.exit(1)

        # Copy test programs to work directory
        work_dir = Path(tempfile.mkdtemp(prefix="sushi-e2e-work-"))
        tmp_dirs.append(str(work_dir))
        shutil.copytree(PROGRAMS_DIR, work_dir, dirs_exist_ok=True)

        # Run tests
        print()
        passed = 0
        failed = 0
        results = []

        for test in TESTS:
            sys.stdout.write(f"  {test['name']} ... ")
            sys.stdout.flush()
            ok, detail = run_test(sushic, work_dir, test)
            if ok:
                print("PASS")
                passed += 1
            else:
                print("FAIL")
                print(f"    {detail}")
                failed += 1
            results.append((test["name"], ok, detail))

        # Summary
        print(f"\n{passed} passed, {failed} failed, {len(TESTS)} total")

        if failed > 0:
            sys.exit(1)

    finally:
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
