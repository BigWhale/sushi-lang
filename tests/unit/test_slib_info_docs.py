"""Phase 3 rendering: both `slib-info` implementations dump the doc record the same way.

`docs/design/documentation.md` section 9 is the authority for the shape, and R5 (the
parameter mode) and R6 (the order and the blank lines) are the rulings locked here.
`test_slib_info_parity.py` keeps an UNDOCUMENTED library, which is the regression that
says a report with no docs in it is unchanged.

The doc blocks are opt-in, so every invocation here asks for them. What the switch
itself does -- and what the plain report leaves out -- is `test_slib_info_flags.py`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_slib_doc_carriage import DOC_LIB, build_library  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TOOL_SRC = REPO / "toolchain" / "src" / "slib_info.sushi"

# The whole of the `hyperspace_jump` entry, which is the one symbol that exercises every
# rule at once: a multi-line body with a blank line in it, one blank line between the
# summary and the body and none before the tags, and parameters in DECLARATION order --
# the source documents `a` before `b` and declares `b` before `a`.
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
"""


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """The documented library, the compiled tool, and what each implementation prints."""
    if shutil.which("sushic") is None:
        pytest.skip("sushic not on PATH")
    tmp = tmp_path_factory.mktemp("docinfo")
    slib, _metadata = build_library(tmp, "doclib", DOC_LIB)

    tool = tmp / "slib-info"
    built = _run(["sushic", str(TOOL_SRC), "-o", str(tool)], cwd=tmp)
    assert built.returncode == 0, built.stdout + built.stderr

    tool_run = _run([str(tool), "--docs", str(slib)])
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr

    env = dict(os.environ)
    env.pop("SUSHI_TOOLCHAIN_BIN", None)
    env["SUSHI_TOOLCHAIN"] = "off"
    py_run = _run(["sushic", "--lib-info", str(slib), "--docs"], env=env)
    assert py_run.returncode == 0, py_run.stdout + py_run.stderr
    return py_run.stdout, tool_run.stdout


def test_the_two_implementations_print_the_same_bytes(report):
    py_out, tool_out = report
    assert py_out.endswith(tool_out)


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_the_whole_function_entry_renders_in_order(report, which):
    """R6, in one assertion: the order, the blank lines, and declaration order."""
    assert JUMP_ENTRY in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_nom_parameter_prints_its_mode(report, which):
    """R5: `nom` is the one mode the type string cannot carry, so the field prints it."""
    assert "  fn shout(nom string s) string\n" in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_symbol_with_no_block_gets_no_blank_line_and_no_placeholder(report, which):
    assert ("    - Errors: When the drive is cold, this fails.\n"
            "  fn plain_add(i32 a, i32 b) i32\n"
            "  fn shout(nom string s) string\n") in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_unit_block_prints_under_its_unit_name(report, which):
    """R12: two spaces further in than the name, in the existing Units section."""
    assert ("Units (1):\n"
            "  doclib\n"
            "    A library that documents every position phase 3 can carry.\n"
            "\n"
            "    The unit block stands first in the file and documents no declaration.\n"
            ) in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_constant_a_struct_and_an_enum_all_dump_their_docs(report, which):
    out = report[which]
    assert ("  const i32 ANSWER\n"
            "    The answer to life, the universe and everything.\n") in out
    assert ("  struct Point:\n"
            "    A point in the plane.\n"
            "\n"
            "    Two coordinates, and nothing else.\n"
            "    i32 x\n"
            "      The distance along x.\n"
            "    i32 y\n"
            "      The distance along y.\n") in out
    assert ("  enum Shade:\n"
            "    How bright a shade is.\n"
            "\n"
            "    Every variant carries its own block.\n"
            "    Plain\n"
            "      No data at all.\n"
            "    Custom(i32)\n"
            "      A brightness from 0 to 255.\n") in out


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_parameter_tag_on_a_struct_is_stored_and_not_printed(report, which):
    """R12: a record with no `params` array renders no parameter line."""
    assert "- Parameter x:" not in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_generic_function_dumps_its_docs_too(report, which):
    """R11: every section that exists prints one; a template has no params array."""
    assert ("  fn pick_bigger<T: Doubler> (template)\n"
            "    Picks the bigger of two doublers.\n"
            "    - Returns: Whichever doubles larger.\n") in report[which]
