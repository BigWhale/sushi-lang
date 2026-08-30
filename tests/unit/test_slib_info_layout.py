"""R38 and R39: the whitespace that makes a documented report scannable.

Measured on a realistic library -- 40 documented functions, 8 structs, 16 fields -- the
report is 428 lines, ten terminal screens, with no blank line anywhere between one symbol
and the next. Whitespace is not decoration at that size; it is the only thing that tells a
reader where one record ends.

Five rules, one per fault:

1. a blank line before the first tag, so prose and contract are not one block;
2. a blank line after a record that printed a block, so the next signature is not glued
   to the last tag of the one above;
3. a hanging indent on a continuation, aligned under the tag's TEXT and not its dash, so
   a wrapped line does not read as a new item;
4. a blank line between tags, because a parameter, a return and an error are three kinds
   of claim;
5. a blank line before a section header, which the report already had.

R39: no reflow. A tag wraps where the author wrote a newline and nowhere else; rule 3
re-indents a continuation that already exists. A reflow would destroy a fenced example.

The PLAIN report is not touched by any of this. Without `--docs` no record prints a block,
so no rule fires and the signature list stays as dense as it was.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_slib_doc_carriage import DOC_LIB, build_library  # noqa: E402
from sushic_path import SUSHIC, SUSHIC_AVAILABLE

REPO = Path(__file__).resolve().parents[2]
TOOL_SRC = REPO / "toolchain" / "src" / "slib_info.sushi"

# The whole `hyperspace_jump` entry and the signature that follows it: one assertion
# covering rules 1 to 4 at once, plus DECLARATION order (the source documents `a` before
# `b` and declares `b` before `a`) and R39 (the body's own blank line survives, and the
# `- Parameter b` text wraps exactly where the author wrote its newline).
JUMP_ENTRY = """\
  fn hyperspace_jump(i32 b, i32 a) i32
    Jumps through hyperspace.

    The drive needs a warm coil.

    The second paragraph of the body.

    - Parameter b: The second one, documented
                   over two lines.

    - Parameter a: The incoming argument.

    - Returns: The jump distance in parsecs.

    - Errors: When the drive is cold, this fails.

  fn plain_add(i32 a, i32 b) i32
"""


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if not SUSHIC_AVAILABLE:
        pytest.skip("no compiler driver in this checkout")
    tmp = tmp_path_factory.mktemp("sliblayout")
    slib, _metadata = build_library(tmp, "doclib", DOC_LIB)
    tool = tmp / "slib-info"
    r = _run([SUSHIC, str(TOOL_SRC), "-o", str(tool)], cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return slib, tool


def _both(slib, tool, *extra):
    tool_run = _run([str(tool), *extra, str(slib)])
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr
    env = dict(os.environ)
    env.pop("SUSHI_TOOLCHAIN_BIN", None)
    env["SUSHI_TOOLCHAIN"] = "off"
    py_run = _run([SUSHIC, "--lib-info", str(slib), *extra], env=env)
    assert py_run.returncode == 0, py_run.stdout + py_run.stderr
    return py_run.stdout, tool_run.stdout


@pytest.fixture(scope="module")
def report(built):
    return _both(*built, "--docs")


@pytest.fixture(scope="module")
def plain(built):
    return _both(*built)


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_the_whole_entry_renders_in_the_new_shape(report, which):
    assert JUMP_ENTRY in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_continuation_hangs_under_the_tag_text(report, which):
    """Rule 3. `- Parameter b: ` is fifteen columns, on top of the record's four."""
    assert "\n" + " " * 19 + "over two lines.\n" in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_two_undocumented_records_stay_adjacent(report, which):
    """Rule 2 closes a BLOCK. Two bare signatures have no block to close."""
    assert ("  fn plain_add(i32 a, i32 b) i32\n"
            "  fn shout(nom string s) string\n") in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_struct_body_is_not_glued_to_its_first_field(report, which):
    """A member is a record too, so rule 2 separates the struct's block from field one."""
    assert ("  struct Point:\n"
            "    A point in the plane.\n"
            "\n"
            "    Two coordinates, and nothing else.\n"
            "\n"
            "    i32 x\n"
            "      The distance along x.\n"
            "\n"
            "    i32 y\n"
            "      The distance along y.\n"
            "\n") in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_section_ends_with_exactly_one_blank_line(report, which):
    """Rule 2 must not double with the blank line a section already prints after itself."""
    assert "\n\n\nEnums (" not in report[which]
    assert "\n\n\nPublic Constants (" not in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_the_plain_report_stays_dense(plain, which):
    """No record prints a block, so no rule fires and the API surface is unchanged."""
    assert ("Public Functions (5):\n"
            "  fn hyperspace_jump(i32 b, i32 a) i32\n"
            "  fn plain_add(i32 a, i32 b) i32\n"
            "  fn shout(nom string s) string\n"
            "  fn checked_jump(i32 factor) i32 | JumpError\n"
            "  fn spin_up(i32 turns) i32\n"
            "\n") in plain[which]


def test_the_two_implementations_agree_in_both_modes(report, plain):
    assert report[0].endswith(report[1])
    assert plain[0].endswith(plain[1])
