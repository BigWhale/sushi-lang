"""The doc-example collector and the wrapper, out of `tests/docs_sweep.py`.

`docs/design/documentation.md` section 10 is the authority: R16 for the fence
attributes, R19 and R20 for the wrapper, R21 for the two skips, R22 for the parsing
collector, and R27 for the nested-fence rule the Markdown collector never had.

The sweep is a by-hand tool, so this module is where its rules are gated. It compiles
a handful of examples end to end, which is slower than the rest of the unit layer and
is the only way to assert that the generated file is a program.
"""
from __future__ import annotations

from pathlib import Path

from tests.docs_sweep import (
    PROJECT_ROOT,
    blocks_in,
    examples_in,
    parse_attrs,
    run_example,
    wrap_example,
)

FIXTURE = PROJECT_ROOT / "tests" / "docs" / "examples" / "fence_outcomes.sushi"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# -- the fence attributes (R16) -------------------------------------------------

def test_a_bare_sushi_fence_runs():
    attrs = parse_attrs("sushi")
    assert attrs.mode == "run"


def test_no_run_compiles_only():
    assert parse_attrs("sushi no_run").mode == "no_run"


def test_skip_carries_its_reason():
    attrs = parse_attrs("sushi skip (needs a listening socket)")
    assert attrs.mode == "skip"
    assert attrs.reason == "needs a listening socket"


def test_skip_with_no_reason_says_so():
    assert parse_attrs("sushi skip").reason == "no reason given"


def test_error_carries_every_code():
    attrs = parse_attrs("sushi error CE1234 CE5678")
    assert attrs.mode == "error"
    assert attrs.codes == ("CE1234", "CE5678")


def test_a_non_sushi_info_string_is_ignored():
    assert parse_attrs("text").mode == "ignore"
    assert parse_attrs("python").mode == "ignore"
    assert parse_attrs("").mode == "ignore"


def test_an_unrecognised_attribute_is_not_silently_dropped():
    """A typo of `no_run` must be visible, so it is its own outcome and not a skip."""
    assert parse_attrs("sushi norun").mode == "unknown"


# -- the wrapper (R19, R20) -----------------------------------------------------

def test_a_snippet_is_wrapped_into_a_helper_and_a_match():
    generated = wrap_example('let i32 d = doubled(21)??\nprintln("{d}")',
                             'use "doubler"', 1)
    assert generated == (
        'use "doubler"\n'
        '\n'
        'fn doc_example_1() ~:\n'
        '    let i32 d = doubled(21)??\n'
        '    println("{d}")\n'
        '    return Result.Ok(~)\n'
        '\n'
        'fn main() i32:\n'
        '    match doc_example_1():\n'
        '        Result.Ok(_) ->\n'
        '            return Result.Ok(0)\n'
        '        Result.Err(_) ->\n'
        '            return Result.Ok(1)\n'
    )


def test_the_wrapper_hoists_a_use_line_out_of_the_body():
    generated = wrap_example('use <collections/hashmap>\nlet i32 d = 1',
                             'use "doubler"', 2)
    assert generated.startswith('use "doubler"\nuse <collections/hashmap>\n\n')
    assert "    use <" not in generated


def test_the_wrapper_does_not_repeat_an_import_the_snippet_already_has():
    generated = wrap_example('use "doubler"\nlet i32 d = 1', 'use "doubler"', 3)
    assert generated.count('use "doubler"') == 1


def test_a_snippet_with_its_own_main_is_wrapped_verbatim():
    snippet = 'fn main() i32:\n    println("Mostly Harmless")\n    return Result.Ok(0)'
    generated = wrap_example(snippet, 'use "doubler"', 4)
    assert generated == 'use "doubler"\n\n' + snippet + "\n"
    assert "doc_example_4" not in generated


def test_a_wrapped_snippet_keeps_blank_lines_blank():
    generated = wrap_example("let i32 a = 1\n\nlet i32 b = 2", "", 5)
    assert "\n    let i32 a = 1\n\n    let i32 b = 2\n" in generated
    assert not generated.startswith("\n")


def test_an_indented_snippet_body_keeps_its_own_shape():
    generated = wrap_example('if (true):\n    println("in")', "", 6)
    assert '    if (true):\n        println("in")\n' in generated


# -- the collector (R21, R22) ---------------------------------------------------

def test_the_collector_finds_every_example_in_source_order():
    examples = examples_in(FIXTURE)
    assert [example.owner for example in examples] == \
        ["doubled", "slow", "connect", "name_length"]
    assert [example.attrs.mode for example in examples] == \
        ["run", "no_run", "skip", "error"]


def test_a_private_declaration_is_a_skip_with_its_reason(tmp_path):
    unit = _write(tmp_path / "hidden.sushi", """\
##:
Doubles a number.
- Example:
```sushi
let i32 d = doubled(21)??
println("{d}")
```
:##
fn doubled(i32 n) i32:
    return Result.Ok(n * 2)
""")
    example = examples_in(unit)[0]
    assert example.skip_reason is not None
    assert "private" in example.skip_reason


def test_a_unit_that_declares_main_is_a_skip_with_its_reason(tmp_path):
    unit = _write(tmp_path / "program.sushi", """\
##:
Doubles a number.
- Example:
```sushi
let i32 d = doubled(21)??
println("{d}")
```
:##
public fn doubled(i32 n) i32:
    return Result.Ok(n * 2)

fn main() i32:
    return Result.Ok(0)
""")
    example = examples_in(unit)[0]
    assert example.skip_reason is not None
    assert "main" in example.skip_reason


def test_a_file_that_does_not_parse_yields_no_examples(tmp_path):
    unit = _write(tmp_path / "broken.sushi", "fn main( i32:\n    nonsense\n")
    assert examples_in(unit) == []


def test_a_defective_example_is_not_collected(tmp_path):
    """CE7007 and CE7008 are the compiler's business; the sweep never runs a defect."""
    unit = _write(tmp_path / "defect.sushi", """\
##:
Doubles a number.
- Example: there is no fence after this tag.
:##
public fn doubled(i32 n) i32:
    return Result.Ok(n * 2)
""")
    assert examples_in(unit) == []


# -- the Markdown collector honours the fence rule (R27) ------------------------

def _illustration(outer_open: str, outer_close: str) -> str:
    return "\n".join([
        "# A page",
        "",
        outer_open,
        "##:",
        "- Example:",
        "```sushi",
        "fn main() i32:",
        "    return Result.Ok(0)",
        "```",
        ":##",
        outer_close,
        "",
    ])


def test_a_tilde_outer_fence_hides_the_example_inside_it():
    assert blocks_in("synthetic.md", _illustration("~~~sushi", "~~~")) == []


def test_a_four_backtick_outer_fence_hides_the_example_inside_it():
    assert blocks_in("synthetic.md", _illustration("````sushi", "````")) == []


def test_an_ordinary_block_is_still_collected():
    text = "\n".join([
        "# A page",
        "",
        "```sushi",
        "fn main() i32:",
        "    return Result.Ok(0)",
        "```",
        "",
    ])
    blocks = blocks_in("synthetic.md", text)
    assert len(blocks) == 1
    assert blocks[0].line == 3


# -- end to end -----------------------------------------------------------------

def test_the_four_outcomes_against_the_checked_in_fixture(tmp_path):
    outcomes = [run_example(example, tmp_path)[1] for example in examples_in(FIXTURE)]
    assert outcomes == ["PASS", "PASS", "SKIP", "EXPECTED-ERROR"]


def test_an_example_that_does_not_compile_is_a_fail(tmp_path):
    unit = _write(tmp_path / "lib" / "drifted.sushi", """\
##:
Doubles a number.
- Example:
```sushi
let i32 d = renamed_last_week(21)??
println("{d}")
```
:##
public fn doubled(i32 n) i32:
    return Result.Ok(n * 2)
""")
    example = examples_in(unit)[0]
    _example, outcome, detail = run_example(example, tmp_path / "run")
    assert outcome == "FAIL", detail
