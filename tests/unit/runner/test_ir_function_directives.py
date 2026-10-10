"""`EXPECT_IR_FUNCTION:` and `EXPECT_IR_NO_FUNCTION:`, the directives that read which
functions the emitted IR defines.

The value is a glob pattern. A pattern with no `$` reads the STEM of a symbol, the text
after the last `$`, so after the unit of the symbol. An extension copy has the stem
`<receiver>_<method>` (`arr__i32_tag`, `Box__string_tag`), so a fixture can name the copy
for one receiver and not the copy for another. A pattern with a `$` reads the whole
symbol, so it can also name the unit. Each directive is run red and green through the
real runner.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from _harness import detail, run_single
from enhanced_test_runner import ir_functions, ir_symbols_matching
from test_metadata import parse_test_metadata

# The copy `tag` is cut for `i32[]`, which a call names, and not for `string[]`.
TWINS = (
    "extend T[] tag() i32:\n"
    "    return 7\n\n"
    "fn main() i32:\n"
    "    let i32[] a = from([1])\n"
    '    let string[] b = from(["x"])\n'
    "    println(a.tag() + b.len())\n"
    "    return 0\n"
)
OUT = '# EXPECT_STDOUT_EXACT: "8\\n"\n'


def _run(tmp_path: Path, header: str):
    path = tmp_path / "test_ir_function_twins.sushi"
    path.write_text(OUT + header + "\n" + TWINS, encoding="utf-8")
    return run_single(path)


CASES = [
    # (id, header, passes)
    ("no function, held", "# EXPECT_IR_NO_FUNCTION: arr__string_tag\n", True),
    ("no function, broken", "# EXPECT_IR_NO_FUNCTION: arr__i32_tag\n", False),
    ("function, held", "# EXPECT_IR_FUNCTION: arr__i32_tag\n", True),
    ("function, broken", "# EXPECT_IR_FUNCTION: arr__string_tag\n", False),
    ("a glob over every receiver", "# EXPECT_IR_NO_FUNCTION: *_tag\n", False),
    ("the method name alone is no stem", "# EXPECT_IR_NO_FUNCTION: tag\n", True),
    ("a symbol with no unit", "# EXPECT_IR_FUNCTION: user_main\n", True),
    ("a pattern with the unit", "# EXPECT_IR_FUNCTION: *$arr__i32_tag\n", True),
    ("a pattern with the wrong unit", "# EXPECT_IR_FUNCTION: other$arr__i32_tag\n", False),
    ("both together",
     "# EXPECT_IR_FUNCTION: arr__i32_tag\n# EXPECT_IR_NO_FUNCTION: arr__string_tag\n",
     True),
]


@pytest.mark.parametrize("header, passes", [c[1:] for c in CASES],
                         ids=[c[0] for c in CASES])
def test_the_directive_decides_the_verdict(tmp_path, header, passes):
    result = _run(tmp_path, header)
    assert result.total_success is passes, detail(result)


def test_the_directive_parses(tmp_path):
    path = tmp_path / "test_ir_function_parse.sushi"
    path.write_text("# EXPECT_IR_NO_FUNCTION: Box__string_tag\n"
                    "# EXPECT_IR_FUNCTION: Box__i32_tag\n\n" + TWINS, encoding="utf-8")
    assert parse_test_metadata(path).ir_function_expectations == [
        ("Box__string_tag", False), ("Box__i32_tag", True)]


@pytest.mark.parametrize("value", ["", "Box__i32 tag", "Box@(i32).tag"])
def test_a_value_that_is_not_one_pattern_is_refused(tmp_path, value):
    path = tmp_path / "test_ir_function_bad.sushi"
    path.write_text(f"# EXPECT_IR_NO_FUNCTION: {value}\n\n" + TWINS, encoding="utf-8")
    meta = parse_test_metadata(path)
    assert meta.ir_function_expectations == []
    assert meta.directive_errors


def test_a_pattern_reads_the_stem_or_the_whole_symbol():
    ir = ('define i32 @"collections$iter$List__i32_enumerate"(i32 %x) {\nstart:\n'
          "  ret i32 %x\n}\n"
          'define i32 @"prog$Box__i32_tag"(i32 %x) {\nstart:\n  ret i32 %x\n}\n'
          "define i32 @main() {\nstart:\n  ret i32 0\n}\n")
    bodies = ir_functions(ir)
    assert ir_symbols_matching(bodies, "List__i32_enumerate") == [
        "collections$iter$List__i32_enumerate"]
    assert ir_symbols_matching(bodies, "*_enumerate") == ["collections$iter$List__i32_enumerate"]
    assert ir_symbols_matching(bodies, "Box__*_tag") == ["prog$Box__i32_tag"]
    assert ir_symbols_matching(bodies, "main") == ["main"]
    assert ir_symbols_matching(bodies, "tag") == []
    assert ir_symbols_matching(bodies, "prog$Box__*") == ["prog$Box__i32_tag"]
    assert ir_symbols_matching(bodies, "iter$List__i32_enumerate") == []
    assert ir_symbols_matching(bodies, "*iter$List__i32_enumerate") == [
        "collections$iter$List__i32_enumerate"]
