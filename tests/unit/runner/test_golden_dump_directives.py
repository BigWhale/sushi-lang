"""`EXPECT_DUMP_SHIR:` and `EXPECT_DUMP_SLIR:`, the golden-dump directives.

A fixture names a golden file beside it. The runner adds the dump flag to the compile of
the fixture, reads the dump that the compile wrote, and compares it with the golden file.
The comparison is exact. The runner owns the dump flags, so COMPILER_FLAGS refuses them.

`--dump-shir` and `--dump-slir` do not exist yet. So these tests name a stub in
SUSHI_COMPILER_STUB (`fixtures/dump_stub.py`): it writes the dumps and runs the real
compiler with the other arguments. Each directive is run red and green through the real
runner, as a fresh process and as a child of the fork-server.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from _harness import FIXTURES_DIR, PROJECT_ROOT, detail, run_single
import enhanced_test_runner
from fork_server import COMPILER_STUB_ENV
from golden_dumps import dump_flags
from test_metadata import parse_test_metadata

STUB = FIXTURES_DIR / "dump_stub.py"
PROGRAM = 'fn main() i32:\n    println("Mostly Harmless")\n    return 0\n'
NO_DUMP = 'fn main() i32:\n    # no dump\n    println("Mostly Harmless")\n    return 0\n'
OUT = '# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n'
SHIR = "shir\nfn main() i32:\n"
SLIR = "slir\nfn main() i32:\n"


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setenv(COMPILER_STUB_ENV, str(STUB))


def _fixture(tmp_path: Path, header: str, body: str = PROGRAM,
             goldens: dict[str, str] | None = None, name: str = "test_dump.sushi") -> Path:
    path = tmp_path / name
    path.write_text(OUT + header + "\n" + body, encoding="utf-8")
    for file_name, text in (goldens or {}).items():
        (tmp_path / file_name).write_text(text, encoding="utf-8")
    return path


def test_both_directives_parse(tmp_path):
    path = _fixture(tmp_path, "# EXPECT_DUMP_SHIR: main.shir\n# EXPECT_DUMP_SLIR: main.slir\n")
    meta = parse_test_metadata(path)
    assert meta.golden_dumps == {"shir": "main.shir", "slir": "main.slir"}
    assert meta.directive_errors == []


@pytest.mark.parametrize("header", [
    "# EXPECT_DUMP_SHIR: sub/main.shir\n",
    "# EXPECT_DUMP_SHIR: main.slir\n",
    "# EXPECT_DUMP_SLIR: main.txt\n",
    "# EXPECT_DUMP_SHIR: .shir\n",
    "# EXPECT_DUMP_SHIR: a.shir\n# EXPECT_DUMP_SHIR: b.shir\n",
], ids=["a directory", "the other suffix", "no suffix", "no stem", "twice"])
def test_a_bad_value_is_a_directive_error(tmp_path, header):
    meta = parse_test_metadata(_fixture(tmp_path, header))
    assert meta.directive_errors, meta


@pytest.mark.parametrize("flag", ["--dump-shir", "--dump-slir"])
def test_compiler_flags_refuses_a_dump_flag(tmp_path, capsys, flag):
    meta = parse_test_metadata(_fixture(tmp_path, f"# COMPILER_FLAGS: {flag}\n"))
    assert flag not in meta.compiler_flags
    assert "runner's to spell" in capsys.readouterr().err


def test_a_fixture_with_no_directive_gets_no_dump_flag(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, ""))
    assert dump_flags(meta, tmp_path / "bin") == []


def test_the_runner_adds_the_flag_with_a_path(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, "# EXPECT_DUMP_SLIR: main.slir\n"))
    flags = dump_flags(meta, Path("/run/bin"))
    assert flags == ["--dump-slir", "/run/bin.slir"], flags


CASES = [
    # (id, header, body, golden files, passes)
    ("shir, held", "# EXPECT_DUMP_SHIR: main.shir\n", PROGRAM, {"main.shir": SHIR}, True),
    ("shir, broken", "# EXPECT_DUMP_SHIR: main.shir\n", PROGRAM,
     {"main.shir": "shir\nfn other() i32:\n"}, False),
    ("shir, one byte short", "# EXPECT_DUMP_SHIR: main.shir\n", PROGRAM,
     {"main.shir": SHIR.rstrip("\n")}, False),
    ("slir, held", "# EXPECT_DUMP_SLIR: main.slir\n", PROGRAM, {"main.slir": SLIR}, True),
    ("slir, broken", "# EXPECT_DUMP_SLIR: main.slir\n", PROGRAM, {"main.slir": SHIR}, False),
    ("both, held", "# EXPECT_DUMP_SHIR: main.shir\n# EXPECT_DUMP_SLIR: main.slir\n",
     PROGRAM, {"main.shir": SHIR, "main.slir": SLIR}, True),
    ("both, one broken", "# EXPECT_DUMP_SHIR: main.shir\n# EXPECT_DUMP_SLIR: main.slir\n",
     PROGRAM, {"main.shir": SHIR, "main.slir": SHIR}, False),
    ("no golden file", "# EXPECT_DUMP_SHIR: main.shir\n", PROGRAM, {}, False),
    ("the compiler wrote no dump", "# EXPECT_DUMP_SHIR: main.shir\n", NO_DUMP,
     {"main.shir": SHIR}, False),
    ("in the fixture's own directory", "# RUN_IN_FIXTURE_DIR\n# EXPECT_DUMP_SHIR: main.shir\n",
     PROGRAM, {"main.shir": SHIR}, True),
]


@pytest.mark.parametrize("header, body, goldens, passes", [c[1:] for c in CASES],
                         ids=[c[0] for c in CASES])
def test_the_directive_decides_the_verdict(stub, tmp_path, header, body, goldens, passes):
    result = run_single(_fixture(tmp_path, header, body, goldens))
    assert result.total_success is passes, detail(result)
    if passes:
        assert result.runtime_success, "the control: the program ran after the dump"


def test_a_difference_is_shown_as_a_diff(stub, tmp_path):
    result = run_single(_fixture(tmp_path, "# EXPECT_DUMP_SHIR: main.shir\n", PROGRAM,
                                 {"main.shir": "shir\nfn other() i32:\n"}))
    assert "main.shir" in result.compilation_message, detail(result)
    assert "-fn other() i32:" in result.compilation_message, detail(result)
    assert "+fn main() i32:" in result.compilation_message, detail(result)


def test_a_child_of_the_fork_server_writes_the_dump(stub, tmp_path):
    _fixture(tmp_path, "# EXPECT_DUMP_SHIR: main.shir\n", PROGRAM, {"main.shir": SHIR})
    with enhanced_test_runner.TestRunner(tmp_path, project_root=PROJECT_ROOT, parallel_jobs=1,
                                         fresh_processes=False) as runner:
        results = runner.run_all_tests()
    assert [r.total_success for r in results.values()] == [True], {
        k: detail(r) for k, r in results.items()}
    assert runner.forked_compiles and not runner.fresh_compiles
