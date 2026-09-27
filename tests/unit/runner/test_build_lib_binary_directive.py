"""`BUILD_LIB_BINARY` builds a fixture's own library as a BINARY `.slib`, through the real runner (#1007).

`BUILD_LIB` and `BUILD_LIB_AT` build `--lib-kind source`. A fixture that must test what a
consumer does with a binary library's manifest (a monomorphized template, for example)
needs the other kind. The test reads the KIND field of the header the runner wrote
(`docs/library-format.md`: a uint32 at offset 24; 1 source, 2 binary). The same reader
reads 1 for a `BUILD_LIB` library, so a reader that answers 2 for every file cannot pass.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[2]

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
from test_metadata import parse_test_metadata  # noqa: E402

MAGIC = "\U0001f363SUSHILIB\U0001f363".encode("utf-8")
KIND_OFFSET = 24
KINDS = {1: "source", 2: "binary", 3: "hybrid"}

USES_LIB = 'use <lib/dep>\n\nfn main() i32:\n    println("{N}")\n    return Result.Ok(0)\n'
DEP = "public const i32 N = 1\n"
OUT_1 = '# EXPECT_STDOUT_EXACT: "1\\n"\n'


def _fixture(tmp_path: Path, header: str, dep: str = DEP) -> Path:
    home = tmp_path / "fixture"
    home.mkdir()
    (home / "dep.sushi").write_text(dep, encoding="utf-8")
    path = home / "test_bin_lib.sushi"
    path.write_text(header + "\n" + USES_LIB, encoding="utf-8")
    return path


def _run(path: Path):
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        return runner.run_single_test(path)


def _detail(result) -> str:
    return f"{result.compilation_message}\n{result.runtime_message}"


def _kind_of(slib: Path) -> str:
    head = slib.read_bytes()[:KIND_OFFSET + 4]
    assert head[:len(MAGIC)] == MAGIC, f"{slib} is not a .slib"
    return KINDS.get(struct.unpack_from("<I", head, KIND_OFFSET)[0], "unknown")


def _record_library_kinds(monkeypatch) -> list[tuple[list[str], str]]:
    """Each library build the runner starts, and the KIND of the file it wrote."""
    built: list[tuple[list[str], str]] = []
    real_run = enhanced_test_runner.subprocess.run

    def _spy(cmd, *args, **kwargs):
        done = real_run(cmd, *args, **kwargs)
        cmd = [str(c) for c in cmd]
        if cmd and cmd[0].endswith("sushic") and "--lib" in cmd and done.returncode == 0:
            built.append((cmd, _kind_of(Path(cmd[cmd.index("-o") + 1]))))
        return done

    monkeypatch.setattr(enhanced_test_runner.subprocess, "run", _spy)
    return built


def test_the_directive_parses(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, "# BUILD_LIB_BINARY: dep.sushi\n"))
    assert meta.build_libs_binary == ["dep.sushi"]
    assert meta.build_libs == []
    assert meta.libs_on_the_path


@pytest.mark.parametrize("directive, kind", [
    ("BUILD_LIB_BINARY", "binary"),
    ("BUILD_LIB", "source"),
], ids=["binary", "control: source"])
def test_the_library_has_the_kind_the_directive_names(tmp_path, monkeypatch, directive, kind):
    built = _record_library_kinds(monkeypatch)
    result = _run(_fixture(tmp_path, f"# {directive}: dep.sushi\n" + OUT_1))
    assert result.total_success, _detail(result)
    assert [k for _, k in built] == [kind], built


def test_without_a_directive_the_library_is_missing(tmp_path):
    result = _run(_fixture(tmp_path, OUT_1))
    assert not result.total_success
    assert "CE3502" in _detail(result), _detail(result)


def test_a_binary_library_that_does_not_build_fails_the_fixture(tmp_path):
    path = _fixture(tmp_path, "# BUILD_LIB_BINARY: dep.sushi\n" + OUT_1,
                    dep="public const i32 N = y\n")
    result = _run(path)
    assert not result.total_success
    assert "BUILD_LIB_BINARY dep.sushi failed" in result.compilation_message, _detail(result)
