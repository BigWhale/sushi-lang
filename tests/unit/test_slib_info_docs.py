"""Phase 3 rendering: both `slib-info` implementations dump the doc record the same way.

`docs/design/documentation.md` section 9 is the authority for the shape, and R5 (the
parameter mode) and R6 (the order and the blank lines) are the rulings locked here.
`test_slib_info_parity.py` keeps an UNDOCUMENTED library, which is the regression that
says a report with no docs in it is unchanged.

The doc blocks are opt-in, so every invocation here asks for them. What the switch
itself does -- and what the plain report leaves out -- is `test_slib_info_flags.py`.

What is locked HERE is that doc text reaches the report from every position an author can
write one in. R6's whitespace half was amended by R38, and the shape of a record now
belongs to `test_slib_info_layout.py`; the ORDER half of R6 survives and stays here.
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
def test_parameters_print_in_declaration_order(report, which):
    """R6's surviving half.

    The source documents `a` before `b` and DECLARES `b` before `a`, so a report that
    walked the doc record's own map would print them the wrong way round.
    """
    out = report[which]
    assert out.index("- Parameter b:") < out.index("- Parameter a:")


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_nom_parameter_prints_its_mode(report, which):
    """R5: `nom` is the one mode the type string cannot carry, so the field prints it."""
    assert "  fn shout(nom string s) string\n" in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_symbol_with_no_block_gets_no_placeholder(report, which):
    """`plain_add` carries no block, so nothing at all stands under its signature.

    The blank line above it is R38 rule 2 closing the block `hyperspace_jump` left,
    which is why `plain_add` and `shout` still touch: neither has a block to close.
    """
    assert ("    - Errors: When the drive is cold, this fails.\n"
            "\n"
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
            "\n"
            "    i32 x\n"
            "      The distance along x.\n"
            "\n"
            "    i32 y\n"
            "      The distance along y.\n") in out
    assert ("  enum Shade:\n"
            "    How bright a shade is.\n"
            "\n"
            "    Every variant carries its own block.\n"
            "\n"
            "    Plain\n"
            "      No data at all.\n"
            "\n"
            "    Custom(i32)\n"
            "      A brightness from 0 to 255.\n") in out


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_parameter_tag_on_a_struct_is_stored_and_not_printed(report, which):
    """R12: a record with no `params` array renders no parameter line."""
    assert "- Parameter x:" not in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_generic_function_dumps_its_docs_too(report, which):
    """R11: every section that exists prints one.

    R46 gave the record its parameter list, so a template now renders the same
    signature a concrete function does. `test_slib_info_sections.py` owns that
    spelling; what is locked here is that a template's tags render at all.
    """
    assert ("  fn pick_bigger@(T: Doubler)(T a, T b) T\n"
            "    Picks the bigger of two doublers.\n"
            "\n"
            "    - Parameter a: The first candidate.\n"
            "\n"
            "    - Returns: Whichever doubles larger.\n") in report[which]
