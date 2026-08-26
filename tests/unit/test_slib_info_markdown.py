"""R44 and R48: the rendered Markdown subset, and an example that finally prints.

**R40 decides where this applies.** In PLAIN mode the marks print as the author wrote
them, exactly as they always have -- nothing piped into a file loses the signal that
`` `spin_up` `` is a symbol and not prose. In COLOUR mode a mark is replaced by the style
it asked for.

**The subset is closed**, and it is narrow because the corpus is: all 61 doc blocks in
this tree, 260 lines, hold 26 lines of inline code, 5 bullets, 2 headings and not one
bold, italic, link or table. Inline code is the only inline construct anyone writes.
`**bold**` and `*italic*` are in because they cost two more branches in a scanner that has
to exist anyway; a link is out because rendering one well means deciding what to do with
the URL, and nothing in the tree has one.

Everything outside the subset prints as the author wrote it, which is what happens to
every construct today, so "out" costs a reader nothing they have now.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_slib_doc_carriage import DOC_LIB, build_library  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TOOL_SRC = REPO / "toolchain" / "src" / "slib_info.sushi"

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _env():
    env = dict(os.environ)
    for name in ("NO_COLOR", "CLICOLOR_FORCE", "TERM", "SUSHI_TOOLCHAIN_BIN"):
        env.pop(name, None)
    env["SUSHI_TOOLCHAIN"] = "off"
    return env


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if shutil.which("sushic") is None:
        pytest.skip("sushic not on PATH")
    tmp = tmp_path_factory.mktemp("slibmd")
    slib, metadata = build_library(tmp, "doclib", DOC_LIB)
    tool = tmp / "slib-info"
    r = _run(["sushic", str(TOOL_SRC), "-o", str(tool)], cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return slib, tool, metadata


def _both(built, *extra):
    slib, tool, _metadata = built
    tool_run = _run([str(tool), "--docs", *extra, str(slib)], env=_env())
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr
    py_run = _run(["sushic", "--lib-info", str(slib), "--docs", *extra], env=_env())
    assert py_run.returncode == 0, py_run.stdout + py_run.stderr
    return py_run.stdout, tool_run.stdout


@pytest.fixture(scope="module")
def plain(built):
    return _both(built)


@pytest.fixture(scope="module")
def painted(built):
    return _both(built, "--color=always")


# --------------------------------------------------------------- the manifest

def test_an_example_carries_its_caption(built):
    """R48 needs the tag's own text, which the record used to drop on the floor."""
    _slib, _tool, metadata = built
    spin = next(f for f in metadata["public_functions"] if f["name"] == "spin_up")
    assert [e.get("caption") for e in spin["doc"]["examples"]] == [
        "the everyday call.",
        "and a second one, whose caption runs over two lines and mentions\n`spin_up` on the second.",
    ]
    assert spin["doc"]["examples"][0]["code"] == (
        'let i32 heat = spin_up(3).realise(0)\nprintln("{heat}")'
    )


# ------------------------------------------------------------------- R48

@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_an_example_prints_its_caption_and_its_code(plain, which):
    assert ("    - Example: the everyday call.\n"
            "        let i32 heat = spin_up(3).realise(0)\n"
            '        println("{heat}")\n'
            "\n"
            "    - Example: and a second one, whose caption runs over two lines and mentions\n"
            "               `spin_up` on the second.\n"
            '        println("{spin_up(1).realise(0)}")\n') in plain[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_an_example_caption_hangs_and_renders_like_any_other_tag(painted, which):
    """It is a tag, so it wraps under its text and its marks become styles."""
    assert ("               \x1b[36mspin_up\x1b[0m on the second.\n") in painted[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_an_example_is_the_last_tag_of_its_record(plain, which):
    """A parameter is a contract and an example is a demonstration, in that order."""
    out = plain[which]
    assert out.index("- Parameter turns:") < out.index("- Example: the everyday")


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_example_code_is_dim(painted, which):
    assert '\x1b[2mlet i32 heat = spin_up(3).realise(0)\x1b[0m' in painted[which]


# ------------------------------------------------------------------- R44

@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_plain_mode_keeps_every_mark(plain, which):
    """R40. A captured report loses no information, and it reads as it always did."""
    out = plain[which]
    assert "Spins the drive up, and reports the `heat` it took." in out
    assert "The word **must** be read as a promise, and *not* as a suggestion." in out


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_colour_mode_replaces_the_marks_with_styles(painted, which):
    out = painted[which]
    assert "Spins the drive up, and reports the \x1b[36mheat\x1b[0m it took." in out
    assert ("The word \x1b[1mmust\x1b[0m be read as a promise, and "
            "\x1b[3mnot\x1b[0m as a suggestion.") in out


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_mark_that_opens_nothing_is_prose(painted, which):
    """A lone `*`, and a `*` with a space after it, are arithmetic and not emphasis."""
    assert "A lone * is" in ANSI.sub("", painted[which])
    assert "prose, and so is 2 * 3 * 4." in ANSI.sub("", painted[which])


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_mark_inside_a_tag_renders_too(painted, which):
    assert "How many turns, at \x1b[36m10\x1b[0m each." in painted[which]


# ------------------------------------------------------------------- parity

@pytest.mark.parametrize("mode", [[], ["--color=always"]], ids=["plain", "colour"])
def test_the_two_implementations_agree(built, mode):
    py_out, tool_out = _both(built, *mode)
    assert py_out.endswith(tool_out)


def test_colour_changes_nothing_but_the_marks(plain, painted):
    """The narrowed invariant.

    Colour still moves no column and drops no line. What it DOES change is a rendered
    mark, which R40 replaces on purpose -- so a line carrying one is exempt, and every
    other line must come back byte for byte once the escapes are stripped.
    """
    stripped = ANSI.sub("", painted[1]).splitlines()
    bare = plain[1].splitlines()
    assert len(stripped) == len(bare)
    for before, after in zip(bare, stripped, strict=True):
        if "`" in before or "*" in before:
            continue
        assert before == after
