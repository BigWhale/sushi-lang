#!/usr/bin/env python3
"""Enhanced test runner for the Sushi language compiler."""

import atexit
from collections import Counter
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
import threading
import time
import os
from tqdm import tqdm

from test_metadata import (parse_test_metadata, get_test_category, should_run_runtime_test,
                          TestMetadata, fixture_binary_name, fixture_id,
                          select_fixtures, is_rebuild_fixture, LIB_FLAG, REBUILD_DIR)
from run_tests import (build_stdlib, build_test_helpers, build_leakcheck,
                       helpers_for_selection,
                       leakcheck_lib_path, leakcheck_platform, COMPILATION_QUARANTINE,
                       DEFAULT_JOBS, JOBS_ENV_VAR, default_jobs,
                       arm_spelling_gate, spelling_gate_tripped,
                       DocGateResult, stdlib_doc_gate, stdlib_override_command)
from fork_server import ForkServer


# Tests whose runtime validation is temporarily quarantined. Compilation is still
# checked; only execution of the compiled binary is skipped. Each entry notes the
# tracking issue; re-enable once the bug is fixed.
RUNTIME_QUARANTINE: set[str] = set()


_NUMERIC = re.compile(r"-?\d+")
# The code of one compiler diagnostic, `error [CE1001]` or `warning [CW1001]`.
_DIAGNOSTIC_CODE = re.compile(r"\b(?:error|warning) \[(C[EW]\d{4})\]")


def diagnostic_codes(stderr: str) -> Counter:
    """Each diagnostic code the compiler printed, with the number of times it printed it."""
    return Counter(_DIAGNOSTIC_CODE.findall(stderr or ""))

# Why a leak assertion was not evaluated. A skip is never a pass, so the reason has to
# survive as far as the summary; these constants are what _check_leaks records and what
# the summary groups by, so the two can never drift apart.
SKIP_NOT_BUILT = "interposer not built"
# The descriptor half of the gate. Both reasons are LOUD: a fd assertion that cannot be
# evaluated is reported as a skip and never as a pass, the rule the byte half follows.
SKIP_NO_FD_REPORT = "interposer reports no descriptor count (stale build)"
SKIP_FD_DIR_UNREADABLE = "the per-process fd directory could not be read"
SKIP_TIMED_OUT = "interposer run timed out"
SKIP_NO_REPORT = "no interposer report"
# macOS and Linux are the two platforms the interposer supports. Anything else used to be
# treated as Linux, so it reported SKIP_NOT_BUILT -- the wrong reason (#275).
SKIP_UNSUPPORTED_PLATFORM = "leak checking not supported on this platform"


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


# One line of a multi-unit compilation's code-generation report: `  geo   [cached]`.
_UNIT_REPORT = re.compile(r"^\s+(\S+)\s+\[(cached|rebuilt)\]\s*$", re.MULTILINE)


def unit_report(stdout: str) -> Dict[str, str]:
    """Each unit a compilation reported, and whether it was `cached` or `rebuilt`."""
    return dict(_UNIT_REPORT.findall(stdout or ""))


@dataclass
class Workspace:
    """The copy of a directory fixture that the runner compiles and runs in (#988)."""
    home: Path
    root: Path
    source: Path
    cache: Optional[Path]
    rebuild: Optional[Path]
    libs: Path


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

    def __init__(self, tests_dir: Path, verbose: bool = False,
                 parallel_jobs: Optional[int] = None, json_output: bool = False,
                 leaks_only: bool = False, allow_leak_skips: bool = False,
                 compile_only: bool = False, project_root: Optional[Path] = None,
                 fresh_processes: bool = True):
        """Initialize the test runner."""
        self.tests_dir = tests_dir
        # The checkout whose `sushic` the run starts. It is the parent of `tests/` for
        # every real run; a runner test that selects from a temporary directory names it.
        self.project_root = project_root or tests_dir.parent
        self.verbose = verbose
        self.parallel_jobs = default_jobs() if parallel_jobs is None else parallel_jobs
        self.json_output = json_output
        self.leaks_only = leaks_only
        self.allow_leak_skips = allow_leak_skips
        self.compile_only = compile_only
        # How a compile starts (#1059): a new `sushic` process for each, or without
        # `fresh_processes` a child of the fork-server. The answer is the same. The
        # command line picks the server unless `--fresh-processes` is given; a caller
        # in this process that wants the server says so.
        self.fresh_processes = fresh_processes
        self._server: Optional[ForkServer] = None
        # The compiles of this run by how they started, as (fixture key, arguments).
        self.forked_compiles: List[Tuple[str, List[str]]] = []
        self.fresh_compiles: List[Tuple[str, List[str]]] = []
        # A run that selected no fixture is a FAILURE, not a pass (#765). An empty
        # result dict reads as "nothing failed" to any caller that counts failures,
        # which is how a chunk that ran nothing reported exit 0 for as long as it did.
        self.selected_nothing = False
        # Every leak assertion that produced a verdict, and every one that could not
        # (as (test name, reason)). Both are lists because they are appended from the
        # worker threads, and list.append is atomic. "Passed" is not evidence a check
        # ran -- reporting the count is what makes the difference visible.
        self.leaks_checked: List[str] = []
        self.leaks_skipped: List[Tuple[str, str]] = []
        self.temp_dir = None
        # The copy each directory fixture compiles and runs in, by `_key`, while it runs.
        self._workspaces: Dict[str, Workspace] = {}
        # The fixture each worker thread runs. The leak re-run reads its directory here.
        self._running = threading.local()
        # The stdlib doc-block gate's verdict (#953), set by main before the run.
        self.doc_gate: Optional[DocGateResult] = None
        # The live tqdm bar, or None. Set only while the bar is on screen, so _emit
        # can tell whether output has to be routed around it.
        self._pbar = None

    def __enter__(self):
        """Create temporary directory for test binaries."""
        self.temp_dir = tempfile.mkdtemp(prefix="sushi_tests_")
        if not self.fresh_processes:
            self._server = ForkServer(self.project_root, Path(self.temp_dir) / "fork-server")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop the fork-server, then clean up temporary directory."""
        if self._server is not None:
            self._server.close()
            self._server = None
        if self.temp_dir and Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def unexcused_leak_skips(self) -> List[Tuple[str, str]]:
        """The leak assertions this run did not evaluate and was not told to excuse.

        A skip is not a pass (#605). The interposer can build and then fail to LOAD --
        a `leakcheck.so` for the wrong architecture is the case that has happened --
        and every EXPECT_NO_LEAKS records a skip while the run reports success. Under
        `--leaks-only` that is a green job over the annotated subset with nothing
        asserted at all. `--allow-leak-skips` is the one escape, for a platform that
        genuinely cannot check.
        """
        if self.allow_leak_skips:
            return []
        return sorted(self.leaks_skipped)

    def _describe_selection(self, filter_pattern: Optional[str]) -> str:
        """The flags that narrowed this run, for the message an empty one prints."""
        parts = []
        if filter_pattern:
            parts.append(f"--filter {filter_pattern!r}")
        if self.leaks_only:
            parts.append("--leaks-only")
        if self.compile_only:
            parts.append("--compile-only")
        return ", ".join(parts) if parts else "the whole corpus"

    def doc_gate_failed(self) -> bool:
        return self.doc_gate is not None and not self.doc_gate.passed

    def _doc_gate_json(self) -> Optional[dict]:
        return None if self.doc_gate is None else self.doc_gate.as_json()

    def _print_doc_gate(self) -> None:
        if self.doc_gate is None:
            return
        colour = (YELLOW if not self.doc_gate.ran
                  else RED if self.doc_gate_failed() else GREEN)
        for line in self.doc_gate.report():
            print(tint(line, colour))

    def run_all_tests(self, filter_pattern: str = None) -> Dict[str, TestResult]:
        """Run every fixture this run selected.

        `select_fixtures` is the one selector: `--filter`, `--leaks-only` and
        `--compile-only` all narrow the same list there, so a flag cannot select a
        corpus a gate has never seen.
        """
        test_files = select_fixtures(self.tests_dir, filter_pattern=filter_pattern,
                                     leaks_only=self.leaks_only,
                                     compile_only=self.compile_only)

        if not test_files:
            self.selected_nothing = True
            selection = self._describe_selection(filter_pattern)
            if self.json_output:
                # A consumer of the JSON reads a report, not an exit code, so the empty
                # selection has to BE a report. Printing nothing would make a consumer
                # crash on an empty file where it should read a clear failure.
                print(json.dumps({
                    "total_tests": 0, "compilation_tests": 0, "runtime_tests": 0,
                    "passed": 0, "failed": 0, "duration_seconds": 0.0,
                    "failed_tests": [],
                    "leak_checks_run": 0, "leak_checks_skipped": 0,
                    "leak_checks_skipped_detail": [],
                    "leak_skips_allowed": self.allow_leak_skips,
                    "selected_nothing": True,
                    "selection": selection,
                    "stdlib_doc_gate": self._doc_gate_json(),
                }, indent=2))
            else:
                self._print_doc_gate()
                print(f"No fixture matched this selection: {selection}.")
                print("A run that covered nothing is a failure, not a pass.")
            return {}

        if not self.json_output:
            print(f"Running {len(test_files)} tests with {self.parallel_jobs} parallel jobs...")
            print("Each compile starts as "
                  + ("a fresh sushic process" if self.fresh_processes
                     else "a child of the fork-server"))
            print(f"Using temporary directory: {self.temp_dir}")
            print()

        start_time = time.time()

        # Run tests in parallel with progress bar
        results = {}
        show_progress = not self.json_output and not self.verbose
        with ThreadPoolExecutor(max_workers=self.parallel_jobs) as executor:
            # Submit all test jobs
            future_to_test = {executor.submit(self.run_single_test, test_file): test_file
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
                    test_file = future_to_test[future]
                    test_name, key = test_file.name, self._key(test_file)
                    try:
                        result = future.result()
                        results[key] = result
                        if not self.json_output and (self.verbose or not result.total_success):
                            self._print_test_result(result)
                    except Exception as e:
                        if not self.json_output:
                            self._emit(f"ERROR: Test {test_name} crashed: {e}")
                        results[key] = TestResult(
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

        # Step 1: compilation
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

        workspace, refusal = self._prepare_workspace(test_file, metadata)
        if refusal is not None:
            return TestResult(name=test_name, category=category, compilation_success=False,
                              compilation_message=refusal, skipped_runtime=True)
        key = self._key(test_file)
        if workspace is not None:
            self._workspaces[key] = workspace
        self._running.test_file = test_file
        try:
            return self._compile_and_run(test_file, test_name, category, metadata)
        finally:
            self._running.test_file = None
            shutil.rmtree(self._scratch_dir(test_file), ignore_errors=True)
            if workspace is not None:
                self._workspaces.pop(key, None)
                shutil.rmtree(workspace.home, ignore_errors=True)

    def _compile_and_run(self, test_file: Path, test_name: str, category: str,
                         metadata: TestMetadata) -> TestResult:
        compilation_success, compilation_message = self._run_compilation_test(test_file, category, metadata)

        result = TestResult(
            name=test_name,
            category=category,
            compilation_success=compilation_success,
            compilation_message=compilation_message
        )

        # Step 2: runtime, for every fixture that runs a binary
        if (compilation_success and
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

    def _key(self, test_file: Path) -> str:
        """A fixture's key in `results` and `_workspaces`: its path under `tests/`."""
        return fixture_id(test_file, self.tests_dir) + test_file.suffix

    def _workspace(self, test_file: Path) -> Optional["Workspace"]:
        key = self._key(test_file)
        return self._workspaces.get(key)

    # --- directory fixtures (#988) -------------------------------------------

    def _prepare_workspace(self, test_file: Path, metadata: TestMetadata
                           ) -> Tuple[Optional["Workspace"], Optional[str]]:
        """A copy of a directory fixture to compile in, or the reason the fixture is refused.

        A fixture needs one when it is a rebuild fixture, when it runs in its own directory,
        or when it builds a library or registers a stdlib module there. The copy keeps the
        tree clean: the runner writes `v2/`, the libraries and the cache only in the copy.
        """
        rebuild = is_rebuild_fixture(test_file)
        # A clean build (THEN_CLEAN_CACHE: source) is the second compilation whose unit
        # report EXPECT_REBUILT / EXPECT_CACHED read; nothing else stands in for `v2/`.
        second_build = (metadata.then_clean_cache == "source"
                        and metadata.expect_stdout_exact_before_rebuild is None)
        if metadata.declares_a_rebuild and not rebuild and not second_build:
            return None, (f"✗ Compilation: the fixture declares a rebuild and its directory "
                          f"holds no {REBUILD_DIR}/")
        if rebuild and (test_file.parent / REBUILD_DIR / test_file.name).exists():
            return None, (f"✗ Compilation: {REBUILD_DIR}/ may not replace the fixture "
                          f"itself; its directives describe both steps")
        if metadata.directive_errors:
            return None, "✗ Compilation: " + "; ".join(metadata.directive_errors)
        refusal = self._refuse_copy_paths(metadata)
        if refusal is not None:
            return None, refusal
        if (metadata.output_path is not None and LIB_FLAG in metadata.compiler_flags
                and should_run_runtime_test(test_file, metadata)):
            return None, (f"✗ Compilation: OUTPUT_PATH with {LIB_FLAG}: a library has no "
                          f"binary to run, so the fixture is a test_err_ or a test_warn_ one")
        if not (rebuild or metadata.run_in_fixture_dir or metadata.libs_on_the_path
                or metadata.stdlib_modules or metadata.reads_the_copy):
            return None, None

        home = Path(self.temp_dir) / "work" / fixture_binary_name(test_file, self.tests_dir)
        root = home / "fixture"
        shutil.copytree(test_file.parent, root,
                        ignore=shutil.ignore_patterns(REBUILD_DIR, "__sushi_cache__"))
        # In its own directory the compiler picks its own cache, as it does for a user,
        # unless the fixture spells one relative to that directory.
        cache: Optional[Path] = home / "cache"
        if metadata.run_in_fixture_dir:
            cache = (Path(metadata.fixture_cache_dir)
                     if metadata.fixture_cache_dir is not None else None)
        return Workspace(home=home, root=root, source=root / test_file.name, cache=cache,
                         rebuild=test_file.parent / REBUILD_DIR if rebuild else None,
                         libs=home / "libs"), None

    @staticmethod
    def _refuse_copy_paths(metadata: TestMetadata) -> Optional[str]:
        """The refusal for a directive that names a path outside the copy, or None.

        FIXTURE_CACHE_DIR and THEN_CLEAN_CACHE spell a path relative to the directory the
        compiler starts in, so they need RUN_IN_FIXTURE_DIR: elsewhere that directory is
        the checkout, and the fixture would write or clean the runner's own tree.
        """
        needs_dir = [name for name, used in (
            ("FIXTURE_CACHE_DIR", metadata.fixture_cache_dir is not None),
            ("THEN_CLEAN_CACHE", metadata.then_clean_cache is not None)) if used]
        if needs_dir and not metadata.run_in_fixture_dir:
            return f"✗ Compilation: {', '.join(needs_dir)} needs RUN_IN_FIXTURE_DIR"
        if metadata.expect_paths_exist_before_clean and metadata.then_clean_cache is None:
            return "✗ Compilation: EXPECT_PATH_EXISTS_BEFORE_CLEAN needs THEN_CLEAN_CACHE"
        named = [path for pair in metadata.build_libs_at for path in pair]
        named += metadata.expect_paths_exist + metadata.expect_paths_absent
        named += metadata.expect_paths_exist_before_clean
        if metadata.fixture_cache_dir is not None:
            named.append(metadata.fixture_cache_dir)
        outside = [path for path in named
                   if not path or Path(path).is_absolute() or ".." in Path(path).parts]
        if outside:
            return ("✗ Compilation: a directive path must be relative and inside the "
                    f"fixture's copy: {', '.join(repr(p) for p in outside)}")
        return None

    def _build_libraries(self, metadata: TestMetadata, workspace: "Workspace") -> Optional[str]:
        """Build each library of the BUILD_LIB family.

        The answer is the failure, or None. A BUILD_LIB, BUILD_LIB_BINARY or
        BUILD_LIB_HYBRID library goes to the directory the runner puts on SUSHI_LIB_PATH,
        with `--lib-kind source`, `binary` or `hybrid`; a BUILD_LIB_WARNS library goes
        there too, as a source library unless its value names the kind; a BUILD_LIB_AT library goes where
        the fixture says, inside the copy, as a source library. A `-> name.slib` form names
        the file in the SUSHI_LIB_PATH directory, and an `@ version` form gives the version.
        A build must exit 0, except a BUILD_LIB_WARNS build: it must exit 1 and print
        exactly the named set of warning codes (#1122). The first four build in
        the order the fixture writes them, whatever the directive, and every BUILD_LIB_AT
        builds after them. Each build sees the directory on its own SUSHI_LIB_PATH, so a
        library may import one built before it. Each
        build has a cache outside the copy, so a library build leaves nothing in it but the
        `.slib`. Every build takes the LIB_FLAGS flags; the fixture's own build does not.
        The version is the written one, else 0.0.0, unless a `nori.toml` in the directory
        the build starts in states one: the compiler reads that file alone and refuses a
        second version (CE3505).
        """
        workspace.libs.mkdir(parents=True, exist_ok=True)
        directive_of = {"source": "BUILD_LIB", "binary": "BUILD_LIB_BINARY",
                        "hybrid": "BUILD_LIB_HYBRID"}
        builds = [("BUILD_LIB_WARNS" if warns else directive_of[kind], kind, source,
                   workspace.libs / slib, version, warns)
                  for kind, source, slib, version, warns in metadata.library_builds]
        builds += [("BUILD_LIB_AT", "source", source, workspace.root / target, None, None)
                   for source, target in metadata.build_libs_at]
        env = {**os.environ, "NO_COLOR": "1"}
        env["SUSHI_LIB_PATH"] = os.pathsep.join(
            [str(workspace.libs), *filter(None, [env.get("SUSHI_LIB_PATH")])])
        for directive, kind, source, target, written, warns in builds:
            target.parent.mkdir(parents=True, exist_ok=True)
            stated = (workspace.home / "nori.toml").is_file()
            version = ([] if stated and written is None
                       else ["--lib-version", written or "0.0.0"])
            done = self._sushic(
                ["--lib", *version, "--lib-kind", kind, *metadata.lib_flags,
                 str(workspace.root / source), "-o", str(target),
                 "--cache-dir", str(workspace.home / "libcache")],
                cwd=workspace.home, env=env, timeout=60)
            if done.returncode != (1 if warns else 0) or not target.is_file():
                return (f"✗ Compilation: {directive} {source} failed with exit "
                        f"{done.returncode}\nSTDERR: {done.stderr.strip()}")
            if warns:
                expected, printed = set(warns), set(diagnostic_codes(done.stderr))
                if printed != expected:
                    return (f"✗ Compilation: {directive} {source} gave other warnings"
                            f"\n  missing: {', '.join(sorted(expected - printed)) or '-'}"
                            f"\n  not expected: {', '.join(sorted(printed - expected)) or '-'}"
                            f"\nSTDERR: {done.stderr.strip()}")
        return None

    def _binary_path(self, test_file: Path, metadata: TestMetadata) -> Path:
        """Where the compilation writes: OUTPUT_PATH in the fixture's copy, else the run's directory.

        The name in the run's directory comes from the fixture's PATH. The stem plus the
        process id was not unique: a run is threads in ONE process, so the id is constant
        and a shared stem was a shared binary (#604).
        """
        workspace = self._workspace(test_file)
        if metadata.output_path is not None and workspace is not None:
            return workspace.root / metadata.output_path
        return Path(self.temp_dir) / fixture_binary_name(test_file, self.tests_dir)

    def _invoke_compiler(self, test_file: Path, metadata: TestMetadata,
                         binary_path: Path, clean: Optional[str] = None
                         ) -> subprocess.CompletedProcess:
        """Start `sushic` for one compilation of a fixture, the way the fixture asks.

        `clean` is a THEN_CLEAN_CACHE form: "bare" starts `sushic --clean-cache` with the
        cache flag alone, "source" starts `sushic --clean-cache <source> -o ...`.

        Force NO_COLOR so diagnostic codes/messages land in stderr without ANSI escapes,
        keeping substring assertions (EXPECT_ERROR_CODE / EXPECT_STDERR_CONTAINS) robust.
        """
        workspace = self._workspace(test_file)
        env = {**os.environ, "NO_COLOR": "1"}
        cwd = self.project_root
        source = str(test_file)
        # A fixture with no copy of its own shares the run's cache: the compiler's
        # default puts it beside the source, in the tree.
        cache_flags: List[str] = ["--cache-dir", str(Path(self.temp_dir) / "cache")]
        output = str(binary_path)
        if workspace is not None:
            cache_flags = []
            source = str(workspace.source)
            if metadata.run_in_fixture_dir:
                cwd, source = workspace.root, workspace.source.name
                if metadata.output_path is not None:
                    output = os.path.relpath(binary_path, workspace.root)
            if workspace.cache is not None:
                cache_flags = ["--cache-dir", str(workspace.cache)]
            if metadata.libs_on_the_path:
                env["SUSHI_LIB_PATH"] = os.pathsep.join(
                    [str(workspace.libs), *filter(None, [env.get("SUSHI_LIB_PATH")])])
        if clean == "bare":
            args = ["--clean-cache", *cache_flags]
        else:
            args = [*(["--clean-cache"] if clean else []), source, "-o", output,
                    *metadata.compiler_flags, *cache_flags]
        if metadata.stdlib_modules and workspace is not None:
            # The bootstrap runs the compiler without the `sushic` wrapper, so it does what
            # the wrapper does: start in the checkout, and name the caller's directory.
            command, extra = stdlib_override_command(
                self.project_root,
                {name: workspace.root / path for name, path in metadata.stdlib_modules.items()},
                args)
            env.update(extra)
            env["SUSHI_CWD"] = str(cwd)
            self.fresh_compiles.append((self._key(test_file), args))
            return subprocess.run(command, capture_output=True, text=True,
                                  cwd=self.project_root, env=env, timeout=30)
        return self._sushic(args, cwd=cwd, env=env, timeout=30,
                            fresh=self.needs_fresh_process(test_file, metadata))

    @staticmethod
    def needs_fresh_process(test_file: Path, metadata: TestMetadata) -> bool:
        """Does this fixture keep a fresh `sushic` process for each compile (#1059)?

        A STDLIB_MODULE fixture starts its own bootstrap and never the wrapper. A fixture
        that asserts what the incremental cache does over two compiles (the rebuild form,
        EXPECT_REBUILT / EXPECT_CACHED, THEN_CLEAN_CACHE) tests it across two processes,
        as a user runs them.
        """
        return bool(metadata.stdlib_modules or is_rebuild_fixture(test_file)
                    or metadata.declares_a_rebuild
                    or metadata.then_clean_cache is not None)

    def _sushic(self, args: List[str], cwd, env: Dict[str, str], timeout: float,
                fresh: bool = False) -> subprocess.CompletedProcess:
        """One compile, as `sushic <args>` started in `cwd` with `env` would give it.

        The fork-server gives the answer unless the run or the fixture asks for a fresh
        process, or the environment differs from the server's where the compiler reads it
        before the fork.
        """
        test_file = getattr(self._running, "test_file", None)
        key = self._key(test_file) if test_file is not None else ""
        if not fresh and self._server is not None:
            done = self._server.run(args, str(cwd), env, timeout)
            if done is not None:
                self.forked_compiles.append((key, args))
                return done
        self.fresh_compiles.append((key, args))
        return subprocess.run([str(self.project_root / "sushic"), *args],
                              capture_output=True, text=True, cwd=cwd, env=env,
                              timeout=timeout)

    def _first_build(self, test_file: Path, metadata: TestMetadata,
                     binary_path: Path) -> Optional[str]:
        """The first compilation of a rebuild fixture, then `v2/` over the copy.

        It must build, into a cache nobody filled, and its binary must print what
        EXPECT_STDOUT_EXACT_BEFORE_REBUILD says. Returns the failure, or None.
        """
        workspace = self._workspaces[self._key(test_file)]
        first = binary_path.with_name(binary_path.name + "__before_rebuild")
        done = self._invoke_compiler(test_file, metadata, first)
        if done.returncode not in (0, 1) or spelling_gate_tripped(done.stderr):
            return (f"✗ Compilation: the build before the rebuild failed with exit "
                    f"{done.returncode}\nSTDERR: {done.stderr.strip()}")
        warm = [name for name, status in unit_report(done.stdout).items()
                if status != "rebuilt"]
        if warm:
            return (f"✗ Compilation: the build before the rebuild found a warm cache: "
                    f"{', '.join(warm)}")
        if metadata.expect_stdout_exact_before_rebuild is not None:
            ran = subprocess.run([str(first)], capture_output=True, text=True,
                                 timeout=metadata.timeout_seconds,
                                 cwd=self._runtime_cwd(test_file, metadata))
            if (ran.returncode != 0
                    or ran.stdout != metadata.expect_stdout_exact_before_rebuild):
                return ("✗ Runtime: the binary before the rebuild\n"
                        f"Expected: exit 0, {metadata.expect_stdout_exact_before_rebuild!r}\n"
                        f"Actual: exit {ran.returncode}, {ran.stdout!r}")
        first.unlink(missing_ok=True)
        assert workspace.rebuild is not None
        shutil.copytree(workspace.rebuild, workspace.root, dirs_exist_ok=True)
        return None

    @staticmethod
    def _check_unit_report(stdout: str, metadata: TestMetadata) -> Tuple[bool, str]:
        """EXPECT_REBUILT and EXPECT_CACHED against the units the compilation reported."""
        reported = unit_report(stdout)
        expected = {**{n: "cached" for n in metadata.expect_cached or []},
                    **{n: "rebuilt" for n in metadata.expect_rebuilt or []}}
        problems = []
        for name, status in sorted(expected.items()):
            got = reported.get(name)
            if got != status:
                problems.append(f"{name}: expected [{status}], "
                                f"got {'[' + got + ']' if got else 'no report'}")
        for name in sorted(set(reported) - set(expected)):
            problems.append(f"{name}: reported [{reported[name]}] and named in neither "
                            f"EXPECT_REBUILT nor EXPECT_CACHED")
        if problems:
            return False, ("✗ Compilation: the rebuild report differs\n  "
                           + "\n  ".join(problems))
        return True, "✓ Compilation: the rebuild report matched"

    def _scratch_dir(self, test_file: Path) -> Path:
        """The empty directory one fixture's binary runs in, inside the run's temp dir."""
        return Path(self.temp_dir) / "scratch" / fixture_binary_name(test_file, self.tests_dir)

    def _runtime_cwd(self, test_file: Optional[Path], metadata: TestMetadata) -> Optional[str]:
        """TEST_CWD (a relative one from the project root), else the fixture's copy for
        RUN_IN_FIXTURE_DIR, else the fixture's scratch directory."""
        if metadata.test_cwd:
            return str(self.project_root / metadata.test_cwd)
        if test_file is None:
            return None
        workspace = self._workspace(test_file)
        if workspace is not None and metadata.run_in_fixture_dir:
            return str(workspace.root)
        scratch = self._scratch_dir(test_file)
        scratch.mkdir(parents=True, exist_ok=True)
        return str(scratch)

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
        workspace = self._workspace(test_file)

        try:
            binary_path = self._binary_path(test_file, metadata)

            builds_libs = metadata.libs_on_the_path or bool(metadata.build_libs_at)
            if workspace is not None and builds_libs:
                failure = self._build_libraries(metadata, workspace)
                if failure is not None:
                    return False, failure
            if workspace is not None and workspace.rebuild is not None:
                failure = self._first_build(test_file, metadata, binary_path)
                if failure is None and builds_libs:
                    failure = self._build_libraries(metadata, workspace)
                if failure is not None:
                    return False, failure

            result = self._invoke_compiler(test_file, metadata, binary_path)

            success = result.returncode == expected_exit_code
            if success and spelling_gate_tripped(result.stderr):
                # #734: the gate refused a diagnostic. The exit code matching the
                # fixture's expectation is a coincidence, not a pass.
                return False, f"\u2717 Compilation: spelling gate\nSTDERR: {result.stderr.strip()}"
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

            report = result.stdout
            if success and metadata.then_clean_cache is not None:
                success, message, report = self._clean_cache(
                    test_file, metadata, binary_path, expected_exit_code, message, report)

            if success and (metadata.expect_rebuilt is not None
                            or metadata.expect_cached is not None):
                success, message = self._check_unit_report(report, metadata)

            if success and workspace is not None:
                success, message = self._check_copy_paths(workspace, metadata, message)

            return success, message

        except subprocess.TimeoutExpired:
            return False, "✗ Compilation: Timeout (30s)"
        except Exception as e:
            return False, f"✗ Compilation: Exception: {e}"

    def _clean_cache(self, test_file: Path, metadata: TestMetadata, binary_path: Path,
                     expected_exit_code: int, message: str,
                     report: str) -> Tuple[bool, str, str]:
        """THEN_CLEAN_CACHE: the fixture's last compiler invocation.

        Returns the verdict, its message, and the stdout whose unit report
        EXPECT_REBUILT / EXPECT_CACHED read: the clean build's for "source", the
        compilation's (`report`) for "bare", which builds nothing. A clean never removes the checkout's own
        cache: the runner fails the fixture when that cache was there before and is gone.
        """
        form = metadata.then_clean_cache
        workspace = self._workspaces[self._key(test_file)]
        missing = [path for path in metadata.expect_paths_exist_before_clean
                   if not (workspace.root / path).exists()]
        if missing:
            return False, ("✗ Compilation: before THEN_CLEAN_CACHE the fixture's copy "
                           f"lacks {', '.join(missing)}"), report
        checkout_cache = self.project_root / "__sushi_cache__"
        existed = checkout_cache.is_dir()
        done = self._invoke_compiler(test_file, metadata, binary_path, clean=form)
        if existed and not checkout_cache.is_dir():
            return False, (f"✗ Compilation: THEN_CLEAN_CACHE: {form} removed the "
                           f"checkout's own cache {checkout_cache}"), done.stdout
        expected = 0 if form == "bare" else expected_exit_code
        if done.returncode != expected or spelling_gate_tripped(done.stderr):
            return False, (f"✗ Compilation: THEN_CLEAN_CACHE: {form} expected exit "
                           f"{expected}, got {done.returncode}\nSTDERR: {done.stderr.strip()}"
                           f"\nSTDOUT: {done.stdout.strip()}"), done.stdout
        return True, message, done.stdout if form == "source" else report

    @staticmethod
    def _check_copy_paths(workspace: "Workspace", metadata: TestMetadata,
                          message: str) -> Tuple[bool, str]:
        """EXPECT_PATH_EXISTS and EXPECT_PATH_ABSENT, against the copy, after the last invocation."""
        problems = [f"{path}: expected to exist, missing"
                    for path in metadata.expect_paths_exist
                    if not (workspace.root / path).exists()]
        problems += [f"{path}: expected absent, present"
                     for path in metadata.expect_paths_absent
                     if (workspace.root / path).exists()]
        if problems:
            return False, ("✗ Compilation: the fixture's copy differs\n  "
                           + "\n  ".join(problems))
        return True, message

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
        if metadata.expect_error_codes_exact is not None:
            expected = Counter(metadata.expect_error_codes_exact)
            printed = diagnostic_codes(stderr)
            if printed != expected:
                differ = "".join(
                    f"\n  {code}: expected {expected[code]}, printed {printed[code]}"
                    for code in sorted(expected.keys() | printed.keys())
                    if expected[code] != printed[code])
                return False, (
                    "✗ Compilation: the diagnostic codes are not the exact multiset"
                    f"{differ}\nSTDERR: {stderr.strip()}"
                )
        return True, "✓ Compilation: diagnostics matched"

    def _run_runtime_test(self, test_name: str, test_file: Path, metadata: TestMetadata) -> Tuple[bool, str]:
        """Run runtime phase for a test."""
        try:
            binary_path = self._binary_path(test_file, metadata)

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
                cwd=self._runtime_cwd(test_file, metadata),
            )

            # Validate runtime behavior
            success, message = self._validate_runtime_result(result, metadata)

            # Leak and descriptor assertions: opt-in per test, enforced whenever the
            # test runs. ONE re-run under the interposer answers both -- it reports the
            # byte balance and the descriptor delta on the same line.
            if metadata.expect_no_leaks or metadata.expect_no_open_fds:
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
        shim = leakcheck_lib_path(self.project_root)
        if shim is None:
            return self._skip_leak_check(test_name, SKIP_UNSUPPORTED_PLATFORM)
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
            cwd=self._runtime_cwd(getattr(self._running, "test_file", None), metadata),
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
            r"(?: double_frees=(\d+) over_freed=(\d+))?"
            r"(?: open_fds=(-?\d+))?",
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

        # The descriptor half, asked first: a leaked descriptor is the defect
        # EXPECT_NO_LEAKS cannot see at all, so it must not be shadowed by the byte
        # report. A shim too old to carry the field, or one whose directory read
        # failed, reports nothing usable -- a loud skip, never a silent pass.
        if metadata.expect_no_open_fds:
            raw_fds = match.group(5)
            if raw_fds is None:
                return self._skip_leak_check(test_name, SKIP_NO_FD_REPORT)
            fd_delta = int(raw_fds)
            if fd_delta < 0:
                return self._skip_leak_check(test_name, SKIP_FD_DIR_UNREADABLE)
            if fd_delta > 0:
                return False, (f"✗ Descriptor check: {fd_delta} descriptor(s) still open "
                               f"at exit")
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
        unexcused = self.unexcused_leak_skips()

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
                "leak_skips_allowed": self.allow_leak_skips,
                "stdlib_doc_gate": self._doc_gate_json(),
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
                if unexcused:
                    print("    " + tint(
                        "This run asserted nothing about those tests, so it FAILS. "
                        "Pass --allow-leak-skips where a skip is expected.", RED))
                else:
                    print("    " + tint("Excused by --allow-leak-skips.", YELLOW))
            self._print_doc_gate()

            if self.doc_gate_failed():
                print("\n" + tint("The stdlib doc-block gate failed!", RED, BOLD))
            if failed_tests == 0 and not unexcused:
                if not self.doc_gate_failed():
                    print("\n" + tint("All tests passed! ✓", GREEN, BOLD))
            elif failed_tests == 0:
                print("\n" + tint(
                    f"{len(unexcused)} leak assertion(s) were not evaluated! ✗",
                    RED, BOLD))
            else:
                print("\n" + tint(f"{failed_tests} test(s) failed! ✗", RED, BOLD))

                # Show failed test details
                print("\n" + tint("Failed tests:", BOLD))
                for name, result in results.items():
                    if not result.total_success:
                        reason = ("Compilation failed" if not result.compilation_success
                                  else "Runtime failed")
                        print(f"  {name}: {tint(reason, RED)}")


def build_parser() -> argparse.ArgumentParser:
    """The runner's options. Each one SELECTS fixtures or shapes the report; none weakens a check."""
    # allow_abbrev=False for the same reason as in run_tests.py: --leaks is gone and
    # must not resolve as a prefix of --leaks-only.
    parser = argparse.ArgumentParser(description="Enhanced Sushi language test runner",
                                     allow_abbrev=False)

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    parser.add_argument(
        "--jobs", "-j",
        type=int,
        default=None,   # None -> TestRunner asks default_jobs(). run_tests always
                        # forwards --jobs, and this module is a SECOND copy of
                        # run_tests when that happens, so evaluating the default here
                        # announced a rejected SUSHI_TEST_JOBS a second time.
        help=f"Number of parallel test jobs (default: {DEFAULT_JOBS}, or ${JOBS_ENV_VAR})"
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

    parser.add_argument(
        "--allow-leak-skips",
        action="store_true",
        help="Report a pass even though leak assertions were not evaluated. Only for a "
             "platform that cannot check at all; a skip is otherwise a failure"
    )

    parser.add_argument(
        "--compile-only",
        action="store_true",
        help="Run only the fixtures that never execute a binary (every test_err_ and "
             "most test_warn_). A SELECTOR, not a weaker check: what it selects is "
             "asserted in full"
    )

    parser.add_argument(
        "--fresh-processes",
        action="store_true",
        help="Start a fresh sushic process for every compile, as a user does, and not a "
             "child of the fork-server. An execution STRATEGY: the answer is the same, "
             "and every check runs"
    )

    return parser


def main():
    """Main entry point for the enhanced test runner."""
    arm_spelling_gate()
    args = build_parser().parse_args()

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
        # A whole run builds every helper; a narrowed one, the helpers it can import.
        narrowed = args.filter or args.leaks_only or args.compile_only
        needed = (helpers_for_selection(tests_dir, select_fixtures(
            tests_dir, filter_pattern=args.filter, leaks_only=args.leaks_only,
            compile_only=args.compile_only)) if narrowed else None)
        if not build_test_helpers(project_root, args.verbose, jobs=args.jobs, only=needed):
            if not args.json:
                print("Failed to build test helpers, aborting tests")
            return 1
        # Unconditional: EXPECT_NO_LEAKS is enforced by every enhanced run, so the
        # interposer is as much a prerequisite as the stdlib. Gating this on a flag is
        # what turned a fresh checkout's 96 leak assertions into 96 silent skips.
        # An unsupported platform is not a build failure: the run continues and every
        # leak assertion records SKIP_UNSUPPORTED_PLATFORM, which the summary reports.
        if leakcheck_platform() is None:
            if not args.json:
                print(f"Leak checking is not supported on {sys.platform}; "
                      "leak assertions will be skipped, and a skip fails the run. "
                      "Pass --allow-leak-skips to report a pass regardless.")
        elif not build_leakcheck(project_root, args.verbose):
            if not args.json:
                print("Failed to build leak-check interposer, aborting tests")
            return 1

    # Set SUSHI_LIB_PATH for library tests
    libs_bin_dir = tests_dir / "libs" / "bin"
    os.environ["SUSHI_LIB_PATH"] = str(libs_bin_dir)

    with TestRunner(tests_dir, args.verbose, args.jobs, args.json,
                    args.leaks_only, args.allow_leak_skips, args.compile_only,
                    fresh_processes=args.fresh_processes) as runner:
        # Ruling 3 on #953: the doc blocks of the bundled stdlib are a runner step.
        runner.doc_gate = stdlib_doc_gate(project_root, filter_pattern=args.filter,
                                          leaks_only=args.leaks_only)
        results = runner.run_all_tests(filter_pattern=args.filter)

    # Exit with appropriate code. Two things besides a failed test count here, and both
    # say the same thing: a run that asserted nothing must not report a pass. A leak
    # assertion that was not evaluated is one (#605); a selection that matched no fixture
    # at all is the other (#765), and an empty result dict would otherwise read as
    # "nothing failed".
    failed_count = sum(1 for r in results.values() if not r.total_success)
    if runner.selected_nothing or runner.doc_gate_failed():
        return 1
    return 0 if failed_count == 0 and not runner.unexcused_leak_skips() else 1


if __name__ == "__main__":
    sys.exit(main())
