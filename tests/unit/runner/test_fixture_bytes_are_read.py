"""A fixture is read as bytes, and a directive block the runner cannot read fails it (#979).

Only the leading comment block holds directives, and it is UTF-8. A later line may hold
any byte: a fixture about a source that is not UTF-8 needs one. A fixture whose directive
block holds a byte that is not UTF-8 FAILS, as a skipped leak check and an empty
selection do; it never passes with its assertions unread.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[2]

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
from test_metadata import parse_test_metadata  # noqa: E402

BODY_WITH_A_BAD_BYTE = b'fn main() i32:\n    let string s = "\xff"\n    return Result.Ok(0)\n'


def _fixture(tmp_path: Path, name: str, data: bytes) -> Path:
    home = tmp_path / "fixture"
    home.mkdir(exist_ok=True)
    path = home / name
    path.write_bytes(data)
    return path


def _run(path: Path):
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        return runner.run_single_test(path)


def test_the_directives_above_a_bad_byte_are_read(tmp_path):
    path = _fixture(tmp_path, "test_err_bytes.sushi",
                    b"# EXPECT_ERROR_CODE: CE3017\n" + BODY_WITH_A_BAD_BYTE)
    metadata = parse_test_metadata(path)
    assert metadata.expect_error_code == ["CE3017"]
    assert metadata.directive_errors == []


def test_a_wrong_code_above_a_bad_byte_fails_the_fixture(tmp_path):
    path = _fixture(tmp_path, "test_err_bytes.sushi",
                    b"# EXPECT_ERROR_CODE: CE2001\n" + BODY_WITH_A_BAD_BYTE)
    result = _run(path)
    assert not result.total_success, (
        f"the fixture passed with its EXPECT_ERROR_CODE unchecked: {result.compilation_message}")
    assert "CE2001" in result.compilation_message


def test_the_right_code_above_a_bad_byte_passes(tmp_path):
    path = _fixture(tmp_path, "test_err_bytes.sushi",
                    b"# EXPECT_ERROR_CODE: CE3017\n" + BODY_WITH_A_BAD_BYTE)
    result = _run(path)
    assert result.total_success, result.compilation_message


def test_a_bad_byte_in_the_directive_block_fails_the_fixture(tmp_path):
    path = _fixture(tmp_path, "test_err_bytes.sushi",
                    b"# EXPECT_ERROR_CODE: CE3017\n# \xff\n" + BODY_WITH_A_BAD_BYTE)
    assert parse_test_metadata(path).directive_errors
    result = _run(path)
    assert not result.total_success
    assert "not valid UTF-8" in result.compilation_message
    assert "line 2" in result.compilation_message


def test_the_corpus_reader_yields_files_only(tmp_path):
    from test_metadata import corpus_files

    (tmp_path / "unit.sushi").mkdir()
    (tmp_path / "unit.sushi" / ".keep").write_text("")
    (tmp_path / "real.sushi").write_text("fn main() i32:\n    return Result.Ok(0)\n")
    assert corpus_files(tmp_path) == [tmp_path / "real.sushi"]


def test_a_file_that_is_not_utf8_is_refused_unless_declared(tmp_path):
    from test_metadata import CorpusReadError, corpus_text

    path = _fixture(tmp_path, "unit.sushi", b"# a unit\n" + BODY_WITH_A_BAD_BYTE)
    with pytest.raises(CorpusReadError, match="not valid UTF-8"):
        corpus_text(path)


def test_a_declared_negative_reads_as_its_directive_block(tmp_path):
    from test_metadata import corpus_text

    unit = _fixture(tmp_path, "unit.sushi", b"# a unit\n" + BODY_WITH_A_BAD_BYTE)
    main = _fixture(tmp_path, "test_err_unit.sushi",
                    b'# EXPECT_ERROR_CODE: CE3017\nuse "unit"\n')
    assert corpus_text(unit) == "# a unit"
    assert corpus_text(main) == '# EXPECT_ERROR_CODE: CE3017\nuse "unit"\n'


def test_a_code_below_the_directives_declares_nothing(tmp_path):
    from test_metadata import CorpusReadError, corpus_text

    unit = _fixture(tmp_path, "unit.sushi", BODY_WITH_A_BAD_BYTE)
    _fixture(tmp_path, "test_err_unit.sushi",
             b'use "unit"\n# EXPECT_ERROR_CODE: CE3017\n')
    with pytest.raises(CorpusReadError):
        corpus_text(unit)
