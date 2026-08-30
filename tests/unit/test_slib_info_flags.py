"""The `slib-info` command line: `--docs` gates the doc blocks, `--help` explains itself.

A report is a reference, and a documented library's report is ten screens before the
prose is added. The doc blocks are therefore opt-in: `--docs` on both implementations,
spelled the same way, because two names for one switch is the drift the parity gate
exists to stop.

`test_slib_info_docs.py` locks what `--docs` prints. This file locks the switch itself.
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

# One line of prose out of the documented library, which no plain report may carry.
A_DOC_LINE = "The answer to life, the universe and everything."


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _env():
    env = dict(os.environ)
    env.pop("SUSHI_TOOLCHAIN_BIN", None)
    env["SUSHI_TOOLCHAIN"] = "off"
    return env


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The documented library and the compiled tool."""
    if not SUSHIC_AVAILABLE:
        pytest.skip("no compiler driver in this checkout")
    tmp = tmp_path_factory.mktemp("slibflags")
    slib, _metadata = build_library(tmp, "doclib", DOC_LIB)
    tool = tmp / "slib-info"
    r = _run([SUSHIC, str(TOOL_SRC), "-o", str(tool)], cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return slib, tool


def test_the_plain_report_carries_no_doc_blocks(built):
    slib, tool = built
    out = _run([str(tool), str(slib)]).stdout
    assert "const i32 ANSWER" in out
    assert A_DOC_LINE not in out
    assert "- Parameter " not in out
    assert "- Returns:" not in out


def test_docs_puts_them_back(built):
    slib, tool = built
    out = _run([str(tool), "--docs", str(slib)]).stdout
    assert A_DOC_LINE in out
    assert "- Returns:" in out


def test_the_flag_may_follow_the_path(built):
    """A flag is a flag wherever it stands; the path is the one bare word."""
    slib, tool = built
    assert (_run([str(tool), str(slib), "--docs"]).stdout
            == _run([str(tool), "--docs", str(slib)]).stdout)


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_explains_itself_and_exits_0(built, flag):
    _slib, tool = built
    r = _run([str(tool), flag])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "usage: slib-info" in r.stdout
    assert "--docs" in r.stdout


def test_help_wins_over_everything_else(built):
    """`--help` is asked for, so it is answered -- no report, whatever else was typed."""
    slib, tool = built
    r = _run([str(tool), "--docs", str(slib), "--help"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Library: doclib" not in r.stdout


def test_an_unknown_option_is_a_usage_error(built):
    _slib, tool = built
    r = _run([str(tool), "--colour"])
    assert r.returncode == 2
    assert "--colour" in r.stderr
    assert "usage: slib-info" in r.stderr


def test_a_second_file_is_a_usage_error(built):
    slib, tool = built
    r = _run([str(tool), str(slib), str(slib)])
    assert r.returncode == 2
    assert "usage: slib-info" in r.stderr


def test_the_fallback_reads_the_same_switch(built):
    """`sushic --lib-info FILE --docs` spells it exactly as the tool does."""
    slib, _tool = built
    plain = _run([SUSHIC, "--lib-info", str(slib)], env=_env())
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert A_DOC_LINE not in plain.stdout

    docs = _run([SUSHIC, "--lib-info", str(slib), "--docs"], env=_env())
    assert docs.returncode == 0, docs.stdout + docs.stderr
    assert A_DOC_LINE in docs.stdout


@pytest.mark.parametrize("extra", [[], ["--docs"]])
def test_the_two_implementations_agree_in_both_modes(built, extra):
    slib, tool = built
    tool_run = _run([str(tool), *extra, str(slib)])
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr
    py_run = _run([SUSHIC, "--lib-info", str(slib), *extra], env=_env())
    assert py_run.returncode == 0, py_run.stdout + py_run.stderr
    assert py_run.stdout.endswith(tool_run.stdout)


@pytest.fixture()
def stub_bin(tmp_path):
    """A stand-in tool that echoes the argument list it was handed."""
    bin_dir = tmp_path / "stub_bin"
    bin_dir.mkdir()
    stub = bin_dir / "slib-info"
    stub.write_text('#!/bin/sh\necho "ARGS: $*"\n')
    stub.chmod(0o755)
    return bin_dir


def test_the_delegation_forwards_the_switch(built, stub_bin):
    """The delegation used to pass the path and nothing else."""
    slib, _tool = built
    env = dict(os.environ)
    env.pop("SUSHI_TOOLCHAIN", None)
    env["SUSHI_TOOLCHAIN_BIN"] = str(stub_bin)

    r = _run([SUSHIC, "--lib-info", str(slib), "--docs"], env=env)
    assert "--docs" in r.stdout

    r = _run([SUSHIC, "--lib-info", str(slib)], env=env)
    assert "--docs" not in r.stdout
