"""The runner's stdlib Python-line gate (#1318): the Python lines under
`sushi_lang/sushi_stdlib/src/` can go down, they cannot go up.

The gate compiles nothing. These tests give it a temporary tree and a temporary baseline
file, and read its verdict.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _harness import PROJECT_ROOT
from run_tests import (
    STDLIB_PYTHON_BASELINE, STDLIB_PYTHON_SRC, stdlib_python_line_count,
    stdlib_python_line_gate,
)


def _tree(root: Path) -> Path:
    src = root / "src"
    (src / "pkg" / "__pycache__").mkdir(parents=True)
    (src / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (src / "pkg" / "b.py").write_text("four\nfive", encoding="utf-8")
    (src / "pkg" / "__pycache__" / "c.py").write_text("x\n" * 50, encoding="utf-8")
    (src / "pkg" / "notes.txt").write_text("y\n" * 50, encoding="utf-8")
    return src


def _gate(tmp_path: Path, baseline: int):
    src = _tree(tmp_path)
    baseline_file = tmp_path / "baseline.txt"
    baseline_file.write_text(f"{baseline}\n", encoding="utf-8")
    return stdlib_python_line_gate(src, baseline_file)


def test_the_count_reads_every_py_file_and_skips_pycache(tmp_path):
    """`wc -l` counts newlines: a last line with no newline is not counted."""
    assert stdlib_python_line_count(_tree(tmp_path)) == 4


def test_a_count_equal_to_the_baseline_passes(tmp_path):
    gate = _gate(tmp_path, 4)
    assert gate.passed, gate.report()
    assert gate.report() == ["Stdlib Python-line gate: 4 line(s), equal to the baseline."]


def test_a_count_above_the_baseline_fails(tmp_path):
    gate = _gate(tmp_path, 3)
    assert not gate.passed
    text = "\n".join(gate.report())
    assert "4 line(s)" in text and "baseline is 3" in text, text
    assert "write the new stdlib function in Sushi" in text, text
    assert "baseline.txt" in text, text


def test_a_count_below_the_baseline_fails(tmp_path):
    gate = _gate(tmp_path, 5)
    assert not gate.passed
    text = "\n".join(gate.report())
    assert "4 line(s)" in text and "baseline is 5" in text, text
    assert "Lower the baseline in" in text and "to 4" in text, text


@pytest.mark.parametrize("content", ["", "many\n"])
def test_a_baseline_that_is_not_a_number_fails(tmp_path, content):
    src = _tree(tmp_path)
    baseline_file = tmp_path / "baseline.txt"
    baseline_file.write_text(content, encoding="utf-8")
    gate = stdlib_python_line_gate(src, baseline_file)
    assert not gate.passed
    assert "is not one number" in "\n".join(gate.report())


def test_a_missing_baseline_fails(tmp_path):
    gate = stdlib_python_line_gate(_tree(tmp_path), tmp_path / "absent.txt")
    assert not gate.passed
    assert "is not one number" in "\n".join(gate.report())


def test_the_count_equals_the_shell_count_of_the_real_tree():
    src = PROJECT_ROOT / STDLIB_PYTHON_SRC
    shell = subprocess.run(
        "find . -name '*.py' -not -path '*__pycache__*' | xargs cat | wc -l",
        shell=True, cwd=src, capture_output=True, text=True, check=True)
    assert stdlib_python_line_count(src) == int(shell.stdout.strip())


def test_the_committed_baseline_is_one_number():
    text = (PROJECT_ROOT / STDLIB_PYTHON_BASELINE).read_text(encoding="utf-8")
    assert text.strip().isdigit(), text
