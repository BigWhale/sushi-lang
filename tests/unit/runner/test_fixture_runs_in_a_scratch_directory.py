"""A fixture binary runs in a scratch directory of its own, inside the run's temp directory.

The runner started every binary in the runner's own working directory, the checkout root
for a developer and for CI. A fixture that writes a relative path (`test_output.txt`, a
probe file, a `.slib`) left that file in the root after the run, and two fixtures that
write one name raced each other under `-j`. Each fixture now gets an empty directory under
the run's temporary directory, and the directory goes with the run. A fixture that reads
the checkout says so with a relative `TEST_CWD`, which is read from the project root.
"""
from __future__ import annotations

from pathlib import Path

from _harness import PROJECT_ROOT
import enhanced_test_runner

PROBE = "scratch_dir_probe.txt"

WRITER = (
    '# EXPECT_STDOUT_EXACT: "{word}\\n"\n'
    "use <io/fs>\n"
    "\n"
    "fn main() i32:\n"
    '    match open("{probe}", FileMode.Write()):\n'
    "        Result.Ok(nom out) ->\n"
    '            out.writeln("{word}")\n'
    "            out.close()\n"
    "        Result.Err(_) ->\n"
    "            return 1\n"
    '    match open("{probe}", FileMode.Read()):\n'
    "        Result.Ok(nom input) ->\n"
    "            match input.readln():\n"
    "                Result.Ok(Maybe.Some(line)) -> println(line)\n"
    "                Result.Ok(Maybe.None) -> return 2\n"
    "                Result.Err(_) -> return 3\n"
    "            input.close()\n"
    "        Result.Err(_) ->\n"
    "            return 4\n"
    "    return 0\n"
)

CHECKOUT_READER = (
    "# TEST_CWD: .\n"
    '# EXPECT_STDOUT_EXACT: "checkout\\n"\n'
    "use <io/files>\n"
    "\n"
    "fn main() i32:\n"
    '    if (is_dir("tests/unit/runner")):\n'
    '        println("checkout")\n'
    "        return 0\n"
    '    println("elsewhere")\n'
    "    return 1\n"
)


def _run(fixtures_dir: Path, jobs: int = 1):
    with enhanced_test_runner.TestRunner(fixtures_dir, project_root=PROJECT_ROOT,
                                         parallel_jobs=jobs) as runner:
        results = runner.run_all_tests()
        run_dir = Path(runner.temp_dir)
    failed = {name: (r.compilation_message, r.runtime_message)
              for name, r in results.items() if not r.total_success}
    return results, failed, run_dir


def _writers(root: Path, words: tuple[str, ...]) -> list[Path]:
    root.mkdir()
    fixtures = []
    for word in words:
        source = root / f"test_scratch_writer_{word}.sushi"
        source.write_text(WRITER.format(word=word, probe=PROBE), encoding="utf-8")
        fixtures.append(source)
    return fixtures


def test_a_relative_write_lands_in_no_directory_the_run_does_not_own(tmp_path, monkeypatch):
    fixtures_dir = tmp_path / "fixtures"
    caller = tmp_path / "caller"
    caller.mkdir()
    _writers(fixtures_dir, ("alpha",))
    monkeypatch.chdir(caller)

    results, failed, run_dir = _run(fixtures_dir)

    assert len(results) == 1
    assert not failed, failed
    for place in (caller, fixtures_dir, PROJECT_ROOT):
        assert not (place / PROBE).exists(), f"the fixture wrote {PROBE} into {place}"
    assert not run_dir.exists(), "the run's temporary directory outlived the run"


def test_two_fixtures_that_write_one_name_do_not_share_it(tmp_path, monkeypatch):
    fixtures_dir = tmp_path / "fixtures"
    caller = tmp_path / "caller"
    caller.mkdir()
    words = ("alpha", "beta", "gamma", "delta")
    _writers(fixtures_dir, words)
    monkeypatch.chdir(caller)

    results, failed, _ = _run(fixtures_dir, jobs=len(words))

    assert len(results) == len(words)
    assert not failed, failed
    assert not (caller / PROBE).exists()


def test_a_relative_test_cwd_is_read_from_the_project_root(tmp_path, monkeypatch):
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    (fixtures_dir / "test_scratch_checkout_reader.sushi").write_text(
        CHECKOUT_READER, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    results, failed, _ = _run(fixtures_dir)

    assert len(results) == 1
    assert not failed, failed
