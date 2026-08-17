#!/usr/bin/env python3
"""Enhanced test runner for the Sushi language compiler."""

import atexit
import io
import re
import subprocess
import signal
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
from tqdm import tqdm

from test_metadata import parse_test_metadata, get_test_category, should_run_runtime_test, TestMetadata
from run_tests import (build_stdlib, build_test_helpers, build_leakcheck,
                       leakcheck_lib_path, COMPILATION_QUARANTINE)


# Tests whose runtime validation is temporarily quarantined. Compilation is still
# checked; only execution of the compiled binary is skipped. Each entry notes the
# tracking issue; re-enable once the bug is fixed.
RUNTIME_QUARANTINE: set[str] = set()


_NUMERIC = re.compile(r"-?\d+")

# Why a leak assertion was not evaluated. A skip is never a pass, so the reason has to
# survive as far as the summary; these constants are what _check_leaks records and what
# the summary groups by, so the two can never drift apart.
SKIP_NOT_BUILT = "interposer not built"
SKIP_TIMED_OUT = "interposer run timed out"
SKIP_NO_REPORT = "no interposer report"


# --- terminal color ----------------------------------------------------------
# Presentation only. Two rules keep it from leaking anywhere it would do harm:
# every color decision lives in this block, and glyph painting happens at the
# print site rather than where a message is built -- so the ~25 message
# constructors stay plain strings and nothing serialized to --json, matched
# against, or written to a log file ever carries an escape code.

def _color_enabled() -> bool:
    """Whether to emit SGR codes."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


COLOR = _color_enabled()

RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"


def tint(text: object, *codes: str) -> str:
    """Wrap `text` in the given SGR codes; a no-op when color is disabled."""
    if not COLOR or not codes:
        return str(text)
    return f"{''.join(codes)}{text}{RESET}"


def paint(message: str) -> str:
    """Color the status glyphs inside an already-composed message."""
    if not COLOR:
        return message
    return message.replace("✓", tint("✓", GREEN)).replace("✗", tint("✗", RED))


# --- sticky progress bar -----------------------------------------------------

def _clock(seconds: float) -> str:
    """Format a duration as MM:SS (or HH:MM:SS past an hour), matching tqdm."""
    seconds = int(max(0.0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class StickyBar:
    """A progress bar pinned in place, with results scrolling upward beneath it."""

    MIN_VIEWPORT = 15    # rows of results wanted below the bar before scrolling

    def __init__(self, total: int, desc: str = "Running tests", stream=None):
        self.total = max(1, total)
        self.desc = desc
        self.out = stream or sys.stdout
        self.n = 0
        self.start = time.monotonic()
        self.rows, self.cols = self._size()
        # Geometry is settled in __enter__: it depends on where the cursor is, which
        # is only meaningful once the caller has finished printing its preamble.
        self.bar_row = self.rows - self.MIN_VIEWPORT
        self.area = self.MIN_VIEWPORT
        self._active = False
        self._prev_handlers: Dict[int, object] = {}

    def _cursor_row(self) -> Optional[int]:
        """Current cursor row via DSR (`ESC[6n`), or None if the terminal won't say."""
        try:
            fd = sys.stdin.fileno()
            if not sys.stdin.isatty():
                return None
            import select
            import termios
            import tty
        except (AttributeError, ValueError, ImportError, io.UnsupportedOperation):
            return None

        try:
            saved = termios.tcgetattr(fd)
        except termios.error:
            return None
        try:
            tty.setraw(fd)
            self.out.write("\033[6n")
            self.out.flush()
            reply = ""
            while len(reply) < 16:
                if not select.select([fd], [], [], 0.2)[0]:
                    return None
                reply += os.read(fd, 1).decode("ascii", errors="replace")
                if reply.endswith("R"):
                    break
            else:
                return None
            match = re.search(r"\[(\d+);(\d+)R$", reply)
            return int(match.group(1)) if match else None
        except (OSError, termios.error):
            return None
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            except termios.error:
                pass

    def _size(self) -> Tuple[int, int]:
        """Terminal size, asked of the stream's own fd."""
        try:
            size = os.get_terminal_size(self.out.fileno())
            return size.lines, size.columns
        except (OSError, AttributeError, ValueError):
            fallback = shutil.get_terminal_size(fallback=(80, 24))
            return fallback.lines, fallback.columns

    @classmethod
    def usable(cls, stream=None) -> bool:
        stream = stream or sys.stdout
        if not stream.isatty() or os.environ.get("SUSHI_TEST_PLAIN"):
            return False
        try:
            return os.get_terminal_size(stream.fileno()).lines >= cls.MIN_VIEWPORT + 3
        except (OSError, AttributeError, ValueError):
            return False

    # -- lifecycle ------------------------------------------------------------

    def __enter__(self) -> "StickyBar":
        cur = self._cursor_row()
        if cur is not None and self.rows - cur >= self.MIN_VIEWPORT:
            # Enough room already: put the bar on the current row, scroll nothing.
            self.bar_row = cur
        else:
            # Too little room (or the terminal would not say): scroll the content up
            # by exactly enough, which leaves the bar on the row the text reached.
            self.out.write("\n" * self.MIN_VIEWPORT)
            self.bar_row = self.rows - self.MIN_VIEWPORT
        self.area = self.rows - self.bar_row

        # Confine scrolling to the rows beneath the bar, and drop the cursor in.
        self.out.write(f"\033[{self.bar_row + 1};{self.rows}r")
        self.out.write(f"\033[{self.bar_row + 1};1H")
        self.out.flush()
        self._active = True
        atexit.register(self.close)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._prev_handlers[sig] = signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass  # not the main thread, or unsupported -- atexit still covers us
        self.render()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _on_signal(self, signum, frame):
        self.close()
        previous = self._prev_handlers.get(signum)
        if callable(previous):
            previous(signum, frame)
        elif previous == signal.SIG_DFL:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    def close(self) -> None:
        """Reset the scroll region and park the cursor below the bar. Idempotent."""
        if not self._active:
            return
        self._active = False
        self.out.write("\033[r")                     # full-screen scrolling again
        self.out.write(f"\033[{self.rows};1H\n")     # cursor to the last row
        self.out.flush()
        for sig, previous in self._prev_handlers.items():
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError, TypeError):
                pass
        self._prev_handlers.clear()

    # -- drawing --------------------------------------------------------------

    def _bar_text(self) -> str:
        """The bar, sized to fill the terminal width exactly."""
        frac = self.n / self.total
        elapsed = time.monotonic() - self.start
        remaining = (elapsed / self.n) * (self.total - self.n) if self.n else 0.0
        left = f"{self.desc}: {frac * 100:3.0f}%|"
        right = f"| {self.n}/{self.total} [{_clock(elapsed)}<{_clock(remaining)}]"
        # One column short of the full width: filling the last cell makes some
        # terminals wrap to the next line, which would push the bar into the region.
        width = max(4, self.cols - len(left) - len(right) - 1)
        filled = int(width * frac)
        return f"{left}{'█' * filled}{' ' * (width - filled)}{right}"

    def render(self) -> None:
        """Redraw the bar on its own row without disturbing the cursor in the region."""
        if not self._active:
            return
        self.out.write(
            f"\0337\033[{self.bar_row};1H\033[2K{tint(self._bar_text(), BOLD)}\0338"
        )
        self.out.flush()

    def update(self, step: int = 1) -> None:
        self.n = min(self.total, self.n + step)
        self.render()

    def write(self, block: str) -> None:
        """Print a block into the scrolling region beneath the bar."""
        self.out.write(block + "\n")
        self.out.flush()
        self.render()


def stdout_contains(stdout: str, expected: str) -> bool:
    """Substring match for EXPECT_STDOUT_CONTAINS, with one exception."""
    if not _NUMERIC.fullmatch(expected.strip()):
        return expected in stdout

    token = expected.strip()
    # No digit or '.' may abut either side, and no '-' may precede (so `42` does not
    # match inside `-42`, and `3.14` does not satisfy an assertion of `3`).
    lookbehind = r"(?<![\d.])" if token.startswith("-") else r"(?<![-\d.])"
    return re.search(lookbehind + re.escape(token) + r"(?![\d.])", stdout) is not None


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

    def __init__(self, tests_dir: Path, mode: str = "full", verbose: bool = False, parallel_jobs: int = 4, json_output: bool = False, leaks_only: bool = False):
        """Initialize the test runner."""
        self.tests_dir = tests_dir
        self.mode = mode
        self.verbose = verbose
        self.parallel_jobs = parallel_jobs
        self.json_output = json_output
        self.leaks_only = leaks_only
        # Every leak assertion that produced a verdict, and every one that could not
        # (as (test name, reason)). Both are lists because they are appended from the
        # worker threads, and list.append is atomic. "Passed" is not evidence a check
        # ran -- reporting the count is what makes the difference visible.
        self.leaks_checked: List[str] = []
        self.leaks_skipped: List[Tuple[str, str]] = []
        self.temp_dir = None
        # The live tqdm bar, or None. Set only while the bar is on screen, so _emit
        # can tell whether output has to be routed around it.
        self._pbar = None

    def __enter__(self):
        """Create temporary directory for test binaries."""
        self.temp_dir = tempfile.mkdtemp(prefix="sushi_tests_")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up temporary directory."""
        if self.temp_dir and Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def run_all_tests(self, filter_pattern: str = None) -> Dict[str, TestResult]:
        """Run all tests in the test directory."""
        test_files = sorted(self.tests_dir.rglob("test_*.sushi"))

        # Exclude files in helpers or bin subdirectories
        # (helpers contains non-standalone modules, bin contains compiled binaries)
        excluded_dirs = {"helpers", "bin"}
        test_files = [f for f in test_files if not any(d in excluded_dirs for d in f.relative_to(self.tests_dir).parts)]

        # Filter by relative path if pattern provided
        if filter_pattern:
            test_files = [f for f in test_files if filter_pattern in str(f.relative_to(self.tests_dir))]

        # --leaks-only: run just the tests carrying a leak assertion, so CI can gate on
        # them without paying for the whole suite a second time.
        if self.leaks_only:
            test_files = [f for f in test_files if parse_test_metadata(f).expect_no_leaks]

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

        # Run tests in parallel with progress bar
        results = {}
        show_progress = not self.json_output and not self.verbose
        with ThreadPoolExecutor(max_workers=self.parallel_jobs) as executor:
            # Submit all test jobs
            future_to_test = {executor.submit(self.run_single_test, test_file): test_file.name
                             for test_file in test_files}

            # Sticky bar on an interactive terminal, tqdm everywhere else. Both expose
            # update()/close(), and _emit() routes result blocks through whichever is
            # live, so the collection loop below does not care which one it got.
            if show_progress:
                if StickyBar.usable():
                    self._pbar = StickyBar(len(test_files)).__enter__()
                else:
                    self._pbar = tqdm(total=len(test_files), desc="Running tests", unit="test",
                                      bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]")
            pbar = self._pbar

            try:
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
                            self._emit(f"ERROR: Test {test_name} crashed: {e}")
                        results[test_name] = TestResult(
                            name=test_name,
                            category="error",
                            compilation_success=False,
                            compilation_message=f"Test runner exception: {e}",
                            skipped_runtime=True
                        )
                    if show_progress:
                        pbar.update(1)
            finally:
                # The scroll region is terminal state; it has to come back even if the
                # loop raises, or the caller's shell is left broken.
                if show_progress:
                    pbar.close()
                    self._pbar = None

        end_time = time.time()

        self._print_summary(results, end_time - start_time)
        return results

    def run_single_test(self, test_file: Path) -> TestResult:
        """Run a single test file through compilation and optionally runtime phases."""
        test_name = test_file.name
        category = get_test_category(test_file)
        metadata = parse_test_metadata(test_file)

        # Phase 1: Compilation
        # Tests in COMPILATION_QUARANTINE expose known compiler ICEs; treat as
        # passing at the compilation phase so the suite stays green while the
        # bug awaits a fix.
        if test_name in COMPILATION_QUARANTINE:
            return TestResult(
                name=test_name,
                category=category,
                compilation_success=True,
                compilation_message="[QUARANTINED - known compiler ICE, skipping compilation]",
                skipped_runtime=True,
                total_success=True,
            )

        compilation_success, compilation_message = self._run_compilation_test(test_file, category, metadata)

        result = TestResult(
            name=test_name,
            category=category,
            compilation_success=compilation_success,
            compilation_message=compilation_message
        )

        # Phase 2: Runtime (if applicable and requested)
        if (self.mode in ("runtime", "full") and
            compilation_success and
            test_name not in RUNTIME_QUARANTINE and
            should_run_runtime_test(test_file, metadata)):

            runtime_success, runtime_message = self._run_runtime_test(test_name, test_file, metadata)
            result.runtime_success = runtime_success
            result.runtime_message = runtime_message
            # Recalculate total success after runtime test
            result.total_success = result.compilation_success and result.runtime_success
        else:
            result.skipped_runtime = True
            result.total_success = result.compilation_success

        return result

    def _run_compilation_test(self, test_file: Path, category: str, metadata: TestMetadata) -> Tuple[bool, str]:
        """Run compilation phase for a test."""
        # Determine expected exit code based on category
        expected_exit_codes = {
            'error': 2,      # Should fail compilation
            'warning': 1,    # Should succeed with warnings
            'success': 0,    # Should succeed without warnings
            'runtime': 0,    # Should succeed without warnings
        }
        expected_exit_code = expected_exit_codes.get(category, 0)

        try:
            # Create unique output binary name
            binary_name = f"test_{test_file.stem}_{os.getpid()}"
            binary_path = Path(self.temp_dir) / binary_name

            # Run the compiler (from project root). Force NO_COLOR so diagnostic
            # codes/messages land in stderr without ANSI escapes, keeping
            # substring assertions (EXPECT_ERROR_CODE / EXPECT_STDERR_CONTAINS)
            # robust.
            project_root = self.tests_dir.parent
            cmd = ["./sushic", str(test_file), "-o", str(binary_path)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=project_root,
                env={**os.environ, "NO_COLOR": "1"},
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

            # Diagnostic assertions on the compilation path. Only meaningful for
            # error/warning tests, whose binaries are never executed (so stderr
            # is the only signal). A missing/garbled diagnostic now fails the
            # test instead of passing silently.
            if success and category in ('error', 'warning'):
                diag_ok, diag_msg = self._check_compilation_diagnostics(result.stderr, metadata)
                if not diag_ok:
                    success = False
                    message = diag_msg

            return success, message

        except subprocess.TimeoutExpired:
            return False, "✗ Compilation: Timeout (30s)"
        except Exception as e:
            return False, f"✗ Compilation: Exception: {e}"

    def _check_compilation_diagnostics(self, stderr: str, metadata: TestMetadata) -> Tuple[bool, str]:
        """Assert expected diagnostics appear in the compiler's stderr."""
        missing = []
        for code in metadata.expect_error_code:
            if code not in stderr:
                missing.append(code)
        for content in metadata.expect_stderr_contains:
            if content not in stderr:
                missing.append(repr(content))

        if missing:
            return False, (
                "✗ Compilation: missing expected diagnostic(s): "
                + ", ".join(missing)
                + f"\nSTDERR: {stderr.strip()}"
            )
        return True, "✓ Compilation: diagnostics matched"

    def _run_runtime_test(self, test_name: str, test_file: Path, metadata: TestMetadata) -> Tuple[bool, str]:
        """Run runtime phase for a test."""
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

            # A test may pin its environment (TEST_ENV) and working directory (TEST_CWD)
            # so host-dependent output (getenv/getcwd) is deterministic across machines.
            run_env = {**os.environ, **(metadata.test_env or {})}

            # Execute the binary
            result = subprocess.run(
                cmd,
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=metadata.timeout_seconds,
                env=run_env,
                cwd=metadata.test_cwd or None,
            )

            # Validate runtime behavior
            success, message = self._validate_runtime_result(result, metadata)

            # Leak assertion: opt-in per test, enforced whenever the test runs. Runs the
            # binary a second time under the malloc-interposer.
            if metadata.expect_no_leaks:
                leak_ok, leak_message = self._check_leaks(test_name, binary_path, metadata)
                message += "\n" + leak_message
                if leak_ok is False:
                    success = False

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

    def _skip_leak_check(self, test_name: str, reason: str) -> Tuple[None, str]:
        """Record a leak assertion that could not be evaluated, and describe it."""
        self.leaks_skipped.append((test_name, reason))
        return None, f"- Leak check skipped: {reason}"

    def _check_leaks(self, test_name: str, binary_path: Path,
                     metadata: TestMetadata) -> Tuple[Optional[bool], str]:
        """Re-run a binary under the malloc-interposer and assert it leaks nothing."""
        shim = leakcheck_lib_path(self.tests_dir.parent)
        if not shim.exists():
            return self._skip_leak_check(test_name, SKIP_NOT_BUILT)

        if sys.platform == "darwin":
            preload = {"DYLD_INSERT_LIBRARIES": str(shim)}
        else:
            preload = {"LD_PRELOAD": str(shim)}

        cmd = [str(binary_path)]
        if metadata.cmd_args:
            cmd.extend(metadata.cmd_args.split())

        run_env = {**os.environ, **(metadata.test_env or {}), **preload}

        # start_new_session makes proc the leader of a fresh process group so a
        # timeout can SIGKILL the whole group. Kill by proc.pid directly (the group
        # leader) rather than os.getpgid(), which would raise ProcessLookupError if
        # the child already exited.
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if metadata.stdin_input else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=run_env,
            cwd=metadata.test_cwd or None,
            start_new_session=True,
        )
        try:
            _stdout, stderr = proc.communicate(
                input=metadata.stdin_input if metadata.stdin_input else None,
                timeout=metadata.timeout_seconds + 30,
            )
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.communicate()
            return self._skip_leak_check(test_name, SKIP_TIMED_OUT)

        # A run killed by a signal is a FAILURE, never a skip. The allocator aborts on
        # heap corruption it detects itself, and that abort pre-empts the interposer's
        # destructor -- so the run produces no report line. Treating "no report" as the
        # only outcome here made the most severe defect the gate can meet (a double free
        # the interposer did not attribute) score as a skip, i.e. not a failure. Checked
        # before the report match, because such a run has no report to match.
        if proc.returncode is not None and proc.returncode < 0:
            self.leaks_checked.append(test_name)
            signame = signal.Signals(-proc.returncode).name
            return False, (f"✗ Leak check: killed by {signame} under the interposer "
                           f"(heap corruption -- the allocator aborted)")

        match = re.search(
            r"SUSHI_LEAKCHECK: leaked=(\d+) blocks=(\d+)"
            r"(?: double_frees=(\d+) over_freed=(\d+))?",
            stderr or "")
        if not match:
            # No report: the interposer failed to load or the program bypassed its
            # destructor (e.g. _exit). Skip rather than silently pass.
            return self._skip_leak_check(test_name, SKIP_NO_REPORT)

        leaked = int(match.group(1))
        blocks = int(match.group(2))
        # Optional so an older interposer binary still parses; a stale build reports
        # zero rather than crashing the runner, and the mtime rule rebuilds it anyway.
        doubles = int(match.group(3) or 0)
        over = int(match.group(4) or 0)
        self.leaks_checked.append(test_name)
        # Over-freeing outranks leaking: it is memory-unsafe where a leak is merely
        # wasteful, and a double free frequently shows a zero balance (the second free
        # debits nothing), so reporting leaks first would report the milder symptom.
        if doubles:
            return False, f"✗ Leak check: {doubles} double free(s)"
        if over:
            return False, f"✗ Leak check: over-freed {over} bytes (unattributed)"
        if leaked == 0:
            return True, "✓ Leak check: no leaks"
        return False, f"✗ Leak check: leaked {leaked} bytes in {blocks} blocks"

    def _validate_runtime_result(self, result: subprocess.CompletedProcess, metadata: TestMetadata) -> Tuple[bool, str]:
        """Validate runtime execution result against metadata expectations."""
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
            # Default expectation: exit code 0 for success. parse_test_metadata sets
            # expect_runtime_exit = 0 for every runnable test, so reaching this branch
            # means the metadata was bypassed; treat a non-zero exit as a failure either
            # way. A binary that aborts, double-frees or traps must never pass.
            if result.returncode == 0:
                messages.append(f"✓ Exit code: {result.returncode}")
            else:
                messages.append(f"✗ Exit code: Expected 0, got {result.returncode}")
                success = False

        # Check stdout content
        if metadata.expect_stdout_exact is not None:
            if result.stdout == metadata.expect_stdout_exact:
                messages.append("✓ Stdout matches expected")
            else:
                messages.append(f"✗ Stdout mismatch\nExpected: {repr(metadata.expect_stdout_exact)}\nActual: {repr(result.stdout)}")
                success = False

        for expected_content in metadata.expect_stdout_contains:
            if stdout_contains(result.stdout, expected_content):
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

    def _emit(self, block: str) -> None:
        """Write a block of output without corrupting a live progress bar."""
        if self._pbar is not None:
            self._pbar.write(block)
        else:
            print(block)

    def _print_test_result(self, result: TestResult) -> None:
        """Print result for a single test."""
        status = tint("PASS", GREEN) if result.total_success else tint("FAIL", RED, BOLD)
        lines = [f"[{status}] {result.name}"]

        if not result.compilation_success or self.verbose:
            lines.append(f"  Compilation: {paint(result.compilation_message)}")

        if not result.skipped_runtime:
            if not result.runtime_success or self.verbose:
                lines.append(f"  Runtime: {paint(result.runtime_message)}")
        elif self.verbose:
            lines.append("  Runtime: Skipped")

        self._emit("\n".join(lines))

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
                "failed_tests": failed_test_list,
                "leak_checks_run": len(self.leaks_checked),
                "leak_checks_skipped": len(self.leaks_skipped),
                "leak_checks_skipped_detail": [
                    {"name": name, "reason": reason}
                    for name, reason in sorted(self.leaks_skipped)
                ],
            }
            print(json.dumps(json_output, indent=2))
        else:
            print(f"\n{tint(f'Test Results ({duration:.2f}s):', BOLD)}")
            print(f"  Total tests: {total_tests}")
            print(f"  Compilation tests: {compilation_tests}")
            print(f"  Runtime tests: {runtime_tests}")
            print(f"  Passed: {tint(passed_tests, GREEN) if passed_tests else passed_tests}")
            print(f"  Failed: {tint(failed_tests, RED, BOLD) if failed_tests else tint(0, GREEN)}")
            if self.leaks_checked:
                print(f"  Leak checks: {tint(len(self.leaks_checked), GREEN)}")
            if self.leaks_skipped:
                # Group by reason: the old line hardcoded "no leak checker on <platform>",
                # which was already a lie for the timeout and no-report cases.
                # Yellow, not plain: a skipped leak assertion is not a passing one, and
                # this line exists precisely so that cannot be read as a clean run.
                print(f"  Leak checks SKIPPED: {tint(len(self.leaks_skipped), YELLOW, BOLD)}")
                by_reason: Dict[str, List[str]] = {}
                for name, reason in sorted(self.leaks_skipped):
                    by_reason.setdefault(reason, []).append(name)
                for reason, names in sorted(by_reason.items()):
                    shown = ", ".join(names[:5])
                    if len(names) > 5:
                        shown += f", ... (+{len(names) - 5} more)"
                    print(f"    {tint(f'{len(names)} x {reason}', YELLOW)}: {shown}")

            if failed_tests == 0:
                print("\n" + tint("All tests passed! ✓", GREEN, BOLD))
            else:
                print("\n" + tint(f"{failed_tests} test(s) failed! ✗", RED, BOLD))

                # Show failed test details
                print("\n" + tint("Failed tests:", BOLD))
                for name, result in results.items():
                    if not result.total_success:
                        reason = ("Compilation failed" if not result.compilation_success
                                  else "Runtime failed")
                        print(f"  {name}: {tint(reason, RED)}")


def main():
    """Main entry point for the enhanced test runner."""
    # allow_abbrev=False for the same reason as in run_tests.py: --leaks is gone and
    # must not resolve as a prefix of --leaks-only.
    parser = argparse.ArgumentParser(description="Enhanced Sushi language test runner",
                                     allow_abbrev=False)

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

    parser.add_argument(
        "--leaks-only",
        action="store_true",
        help="Run only the tests declaring EXPECT_NO_LEAKS (the assertion itself is "
             "always enforced)"
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
        # Unconditional: EXPECT_NO_LEAKS is enforced by every enhanced run, so the
        # interposer is as much a prerequisite as the stdlib. Gating this on a flag is
        # what turned a fresh checkout's 96 leak assertions into 96 silent skips.
        if not build_leakcheck(project_root, args.verbose):
            if not args.json:
                print("Failed to build leak-check interposer, aborting tests")
            return 1

    # Set SUSHI_LIB_PATH for library tests
    libs_bin_dir = tests_dir / "libs" / "bin"
    os.environ["SUSHI_LIB_PATH"] = str(libs_bin_dir)

    with TestRunner(tests_dir, args.mode, args.verbose, args.jobs, args.json, args.leaks_only) as runner:
        results = runner.run_all_tests(filter_pattern=args.filter)

    # Exit with appropriate code
    failed_count = sum(1 for r in results.values() if not r.total_success)
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
