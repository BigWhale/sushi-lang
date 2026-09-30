"""The fork-server gives the answer of a fresh `sushic` process (#1059, guard 2).

The runner compiles each fixture in a child of one server process, which imported the
compiler and built its grammar once. That is an execution STRATEGY and not a weaker
mode: for each fixture of a fixed sample, both strategies must give the same exit code,
stdout and stderr for every compile, and the same verdict, leak result and descriptor
result for the fixture. The sample holds an error, a warning, a success, a leak, a
descriptor, a multi-unit and stdin, and two library fixtures, and the two classes that
keep a fresh process. Direct requests hold the CLI paths no fixture reaches: `--version`,
a usage error, no source, a relative path from another directory, and a raised
diagnostic with `--traceback`. A child that a signal stops, and a child past its
timeout, are failures.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from _harness import TESTS_DIR, PROJECT_ROOT
import enhanced_test_runner
import fork_server
import run_tests

SAMPLE = [
    "generics/builtin_methods/test_err_result_is_ok_arity.sushi",
    "docs/blocks/test_warn_missing_docs.sushi",
    "generics/user_generic_struct/test_user_generic_struct_pair.sushi",
    "generics/hashmap_array_value/test_hashmap_dynamic_array_value.sushi",
    "memory/generic_drop/test_run_generic_drop_holds_a_handle.sushi",
    "io/lines/test_stdin_lines_demo.sushi",
    "libs/source_lib_cache_dir/test_lib_source_lib_cache_dir.sushi",
    "libs/library_lints/test_err_lib_template_error_keeps_its_note.sushi",
]
# The two classes that keep a fresh process in either strategy.
FRESH_CLASSES = [
    "cache/constant_value/test_cache_constant_value.sushi",
    "libs/library_lints/test_stdlib_unit_lints_stay_with_author.sushi",
]
_TIMING = re.compile(r"\b\d+\.\d+s\b")


class _Recording(enhanced_test_runner.TestRunner):
    """The one runner, with a record of every compile it started through `_sushic`."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def _sushic(self, args, cwd, env, timeout, fresh=False):
        done = super()._sushic(args, cwd, env, timeout, fresh)
        self.records.append((self._key(self._running.test_file), args,
                             done.returncode, done.stdout, done.stderr))
        return done


def _normal(text, temp_dir):
    return _TIMING.sub("<T>s", str(text).replace(temp_dir, "<RUN>"))


def _run_sample(fresh: bool, fixtures):
    with _Recording(TESTS_DIR, parallel_jobs=4, fresh_processes=fresh) as runner:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = dict(zip(fixtures, pool.map(
                runner.run_single_test, [TESTS_DIR / f for f in fixtures]), strict=True))
        temp = runner.temp_dir
        verdicts = {f: (r.compilation_success, _normal(r.compilation_message, temp),
                        r.runtime_success, _normal(r.runtime_message, temp),
                        r.skipped_runtime, r.total_success)
                    for f, r in results.items()}
        compiles = sorted((key, [_normal(a, temp) for a in args], rc,
                           _normal(out, temp), _normal(err, temp))
                          for key, args, rc, out, err in runner.records)
        return (verdicts, compiles, sorted(runner.leaks_checked),
                sorted(runner.leaks_skipped), runner)


@pytest.fixture(scope="module")
def interposer():
    if run_tests.leakcheck_platform() is None:
        pytest.skip("no leak interposer on this platform")
    lib = run_tests.leakcheck_lib_path(PROJECT_ROOT)
    if lib is None or not lib.exists():
        assert run_tests.build_leakcheck(PROJECT_ROOT), "the interposer did not build"
    return lib


def test_both_strategies_give_one_answer_over_the_sample(interposer):
    fixtures = SAMPLE + FRESH_CLASSES
    forked = _run_sample(False, fixtures)
    fresh = _run_sample(True, fixtures)
    assert forked[0] == fresh[0], "a fixture's verdict differs between the strategies"
    assert forked[1] == fresh[1], "a compile's exit code, stdout or stderr differs"
    assert forked[2] == fresh[2] and forked[3] == fresh[3], "the leak checks differ"
    # The controls: the sample passes, its leak checks ran, and the server compiled.
    assert all(v[-1] for v in fresh[0].values()), fresh[0]
    assert len(fresh[2]) >= 3 and not fresh[3], (fresh[2], fresh[3])
    assert forked[4].forked_compiles and not fresh[4].forked_compiles


def test_the_fresh_classes_keep_a_fresh_process():
    with _Recording(TESTS_DIR, parallel_jobs=1, fresh_processes=False) as runner:
        for fixture in SAMPLE[:1] + FRESH_CLASSES:
            assert runner.run_single_test(TESTS_DIR / fixture).total_success, fixture
    forked = {key for key, _ in runner.forked_compiles}
    fresh = {key for key, _ in runner.fresh_compiles}
    assert forked == {SAMPLE[0]}, forked
    assert fresh == set(FRESH_CLASSES), fresh


@pytest.fixture
def server(tmp_path):
    handle = fork_server.ForkServer(PROJECT_ROOT, tmp_path / "server")
    try:
        yield handle
    finally:
        handle.close()


def _fresh(args, cwd, env, timeout=60):
    return subprocess.run([str(PROJECT_ROOT / "sushic"), *args], capture_output=True,
                          text=True, cwd=cwd, env=env, timeout=timeout)


def _same(forked, fresh):
    return ((forked.returncode, _TIMING.sub("<T>s", forked.stdout),
             _TIMING.sub("<T>s", forked.stderr))
            == (fresh.returncode, _TIMING.sub("<T>s", fresh.stdout),
                _TIMING.sub("<T>s", fresh.stderr)))


def _program(tmp_path: Path) -> Path:
    home = tmp_path / "prog"
    home.mkdir()
    (home / "dep.sushi").write_text("public const i32 N = 7\n", encoding="utf-8")
    (home / "main.sushi").write_text(
        'use "dep"\n\nfn main() i32:\n    println("{N}")\n    return 0\n',
        encoding="utf-8")
    return home


def test_the_cli_paths_no_fixture_reaches(server, tmp_path):
    home = _program(tmp_path)
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    env = {**os.environ, "NO_COLOR": "1"}
    cases = [
        (["--version"], PROJECT_ROOT),
        (["--no-such-flag"], PROJECT_ROOT),
        ([], PROJECT_ROOT),
        # A relative source and output: the compiler starts in the caller's directory.
        (["main.sushi", "-o", "out/bin", "--cache-dir", "c"], home),
        # A raised diagnostic with the Python traceback appended after it.
        (["main.sushi", "-o", "bin2", "--cache-dir", str(blocker), "--traceback"], home),
    ]
    for args, cwd in cases:
        forked = server.run(args, str(cwd), env, 60)
        shutil.rmtree(home / "c", ignore_errors=True)
        shutil.rmtree(home / "out", ignore_errors=True)
        fresh = _fresh(args, cwd, env)
        shutil.rmtree(home / "c", ignore_errors=True)
        assert forked is not None
        assert _same(forked, fresh), (args, forked, fresh)
    assert "Traceback" in fresh.stderr + fresh.stdout, "the control: a traceback printed"


def test_a_child_stopped_by_a_signal_is_a_failure(server, tmp_path):
    fifo = tmp_path / "blocks.sushi"
    os.mkfifo(fifo)
    pids = []

    def stop(pid):
        pids.append(pid)
        time.sleep(0.5)
        os.kill(pid, signal.SIGSEGV)

    done = server.run([str(fifo), "-o", str(tmp_path / "bin")], str(tmp_path),
                      dict(os.environ), 60, on_start=stop)
    assert pids and done is not None
    assert done.returncode == -signal.SIGSEGV, done


def test_a_child_past_its_timeout_is_killed(server, tmp_path):
    fifo = tmp_path / "waits.sushi"
    os.mkfifo(fifo)
    pids = []
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        server.run([str(fifo), "-o", str(tmp_path / "bin")], str(tmp_path),
                   dict(os.environ), 1, on_start=pids.append)
    assert time.monotonic() - started < 30
    assert pids and not _alive(pids[0])


def _alive(pid: int) -> bool:
    # The server reaps the child before it replies, so the process id is free again.
    if sys.platform.startswith("linux"):
        return os.path.exists(f"/proc/{pid}")
    return subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0


def _threads(pid: int) -> int:
    if sys.platform.startswith("linux"):
        return len(os.listdir(f"/proc/{pid}/task"))
    listing = subprocess.run(["ps", "-M", "-p", str(pid)], capture_output=True, text=True)
    return len(listing.stdout.strip().splitlines()) - 1


def test_the_server_is_single_threaded_when_it_forks(server, tmp_path):
    assert server.run(["--version"], str(tmp_path), dict(os.environ), 60) is not None
    assert _threads(server.server_pid) == 1


def test_an_environment_read_before_the_fork_takes_a_fresh_process(server, tmp_path):
    assert server.run(["--version"], str(tmp_path), dict(os.environ), 60) is not None
    for name in fork_server.IMPORT_TIME_ENV:
        other = {**os.environ, name: str(tmp_path / "elsewhere")}
        assert server.run(["--version"], str(tmp_path), other, 60) is None, name
