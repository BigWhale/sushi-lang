"""`EXPECT_IR_HOLDS:` and `EXPECT_IR_LACKS:`, the directives that read the emitted IR.

A fixture names one function and one text. The runner asks the compiler for the IR and
reads the body of that function alone, so a marked function and its unmarked twin in one
program each get their own verdict. Each directive is run red and green through the real
runner.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from _harness import detail, run_single
from enhanced_test_runner import ir_function, ir_functions
from test_metadata import parse_test_metadata

TWINS = (
    'fn marked(i32[] xs) i32 dont_panic because "the caller passes a non-empty array":\n'
    "    return xs[0]\n\n"
    "fn checked(i32[] xs) i32:\n"
    "    return xs[0]\n\n"
    "fn main() i32:\n"
    "    let i32[] xs = from([0])\n"
    "    return marked(xs) + checked(xs)\n"
)
FLAG = "# COMPILER_FLAGS: --dont-panic\n"


def _run(tmp_path: Path, name: str, header: str, body: str = TWINS):
    path = tmp_path / name
    path.write_text(header + "\n" + body, encoding="utf-8")
    return run_single(path)


CASES = [
    # (id, header, passes)
    ("lacks, held", FLAG + "# EXPECT_IR_LACKS: marked _bounds_fail\n", True),
    ("lacks, broken", FLAG + "# EXPECT_IR_LACKS: checked _bounds_fail\n", False),
    ("holds, held", FLAG + "# EXPECT_IR_HOLDS: checked _bounds_fail\n", True),
    ("holds, broken", FLAG + "# EXPECT_IR_HOLDS: marked _bounds_fail\n", False),
    ("a function the IR does not define", FLAG + "# EXPECT_IR_LACKS: absent _bounds_fail\n",
     False),
]


@pytest.mark.parametrize("header, passes", [c[1:] for c in CASES],
                         ids=[c[0] for c in CASES])
def test_the_directive_decides_the_verdict(tmp_path, header, passes):
    result = _run(tmp_path, "test_ir_twins.sushi", header)
    assert result.total_success is passes, detail(result)


def test_a_program_of_more_than_one_unit_writes_its_ir(tmp_path):
    """A stdlib import makes a second unit, and a second unit builds incrementally."""
    body = "use <collections/iter>\n\n" + TWINS
    result = _run(tmp_path, "test_ir_units.sushi",
                  FLAG + "# EXPECT_IR_LACKS: marked _bounds_fail\n", body)
    assert result.total_success, detail(result)


def test_the_directive_parses(tmp_path):
    path = tmp_path / "test_ir_parse.sushi"
    path.write_text("# EXPECT_IR_LACKS: marked _bounds_fail\n"
                    '# EXPECT_IR_HOLDS: checked "br i1"\n\n' + TWINS, encoding="utf-8")
    assert parse_test_metadata(path).ir_expectations == [
        ("marked", "_bounds_fail", False), ("checked", "br i1", True)]


def test_a_directive_with_no_text_is_refused(tmp_path):
    path = tmp_path / "test_ir_bad.sushi"
    path.write_text("# EXPECT_IR_LACKS: marked\n\n" + TWINS, encoding="utf-8")
    meta = parse_test_metadata(path)
    assert meta.ir_expectations == []
    assert meta.directive_errors


def test_a_function_is_found_behind_its_unit():
    ir = ('define internal i32 @"prog$head"(i32 %x) {\nstart:\n  ret i32 %x\n}\n'
          "define i32 @main() {\nstart:\n  ret i32 0\n}\n")
    bodies = ir_functions(ir)
    assert ir_function(bodies, "head") == ["prog$head"]
    assert ir_function(bodies, "main") == ["main"]
    assert ir_function(bodies, "ead") == []
