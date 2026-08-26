"""The report says what the manifest carries: `@(...)`, full signatures, every section.

Four faults, measured on this tree and fixed together because they are wrong in the same
renderer:

- a generic printed `fn pick_bigger<T: Doubler> (template)` -- angle brackets in
  user-visible text, which `docs/design/type-identity.md` reserves for the INTERNAL
  identity name;
- a generic printed no parameter list, so its `- Parameter` tags could never render;
- four manifest sections had no renderer at all: generic structs, generic enums, perks
  and perk implementations;
- a function's error arm was not in the manifest, so `i32 | JumpError` printed as `i32`.

The manifest keeps `<...>`: `parse_type_string` reads those strings back, so the
spelling is a wire format. The RENDERER converts, which is the rule
`display_type_name` already carries on the Python side.
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
def built(tmp_path_factory):
    if shutil.which("sushic") is None:
        pytest.skip("sushic not on PATH")
    tmp = tmp_path_factory.mktemp("slibsections")
    slib, metadata = build_library(tmp, "doclib", DOC_LIB)
    tool = tmp / "slib-info"
    r = _run(["sushic", str(TOOL_SRC), "-o", str(tool)], cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return slib, tool, metadata


@pytest.fixture(scope="module")
def report(built):
    """What each implementation prints with the doc blocks in."""
    slib, tool, _metadata = built
    tool_run = _run([str(tool), "--docs", str(slib)])
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr
    env = dict(os.environ)
    env.pop("SUSHI_TOOLCHAIN_BIN", None)
    env["SUSHI_TOOLCHAIN"] = "off"
    py_run = _run(["sushic", "--lib-info", str(slib), "--docs"], env=env)
    assert py_run.returncode == 0, py_run.stdout + py_run.stderr
    return py_run.stdout, tool_run.stdout


# ---------------------------------------------------------------- the manifest

def test_the_error_arm_is_carried(built):
    """A signature that says `| JumpError` has to travel, or no renderer can print it."""
    _slib, _tool, metadata = built
    by_name = {f["name"]: f for f in metadata["public_functions"]}
    assert by_name["checked_jump"]["error_type"] == "JumpError"


def test_a_function_with_no_error_arm_carries_no_key(built):
    """The default is StdError and the signature does not say it, so neither does the record."""
    _slib, _tool, metadata = built
    by_name = {f["name"]: f for f in metadata["public_functions"]}
    assert "error_type" not in by_name["plain_add"]


def test_a_generic_function_carries_its_signature(built):
    """R46: the params array is what makes a `- Parameter` tag renderable."""
    _slib, _tool, metadata = built
    generic = metadata["templates"]["generic_functions"]
    pick = next(g for g in generic if g["name"] == "pick_bigger")
    assert [p["name"] for p in pick["params"]] == ["a", "b"]
    assert [p["type"] for p in pick["params"]] == ["T", "T"]
    assert pick["return_type"] == "T"


def test_the_manifest_keeps_the_internal_spelling(built):
    """`parse_type_string` reads these back, so `<...>` is a wire format and stays."""
    _slib, _tool, metadata = built
    assert metadata["templates"]["generic_structs"][0]["name"] == "Box"


# ------------------------------------------------------------------ the report

@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_no_angle_bracket_reaches_the_reader(report, which):
    """R45. `->` is the one arrow a type may spell, and no type here has one."""
    for line in report[which].splitlines():
        assert "<" not in line or line.lstrip().startswith("<"), line


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_generic_function_renders_its_whole_signature(report, which):
    """R45 and R46 together: `@(...)`, the parameters, and the tags they unlock."""
    assert ("  fn pick_bigger@(T: Doubler)(T a, T b) T\n"
            "    Picks the bigger of two doublers.\n") in report[which]
    assert "- Parameter a: The first candidate.\n" in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_function_prints_its_error_arm(report, which):
    assert "  fn checked_jump(i32 factor) i32 | JumpError\n" in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_function_with_no_error_arm_prints_none(report, which):
    assert "  fn plain_add(i32 a, i32 b) i32\n" in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_the_generic_struct_section_exists(report, which):
    """R47. `Box@(T)` was in the manifest and in neither implementation's report."""
    assert ("Generic Structs (1):\n"
            "  struct Box@(T):\n"
            "    A box that holds one value.\n") in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_the_generic_enum_section_exists(report, which):
    assert ("Generic Enums (1):\n"
            "  enum Either@(T):\n"
            "    Either one thing or the other.\n") in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_the_perk_section_exists(report, which):
    assert ("Perks (1):\n"
            "  perk Doubler:\n"
            "    Doubles a number, and says so.\n") in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_the_perk_implementation_section_exists(report, which):
    """Each method carries its own block, which is the one thing an impl record adds."""
    assert ("Perk Implementations (1):\n"
            "  extend i32 with Doubler:\n"
            "    The i32 side of the doubler contract.\n"
            "    fn doubled\n"
            "      Twice the receiver, by multiplication.\n") in report[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_an_empty_section_is_suppressed(report, which):
    """The existing convention: a section with nothing in it does not print its header."""
    assert "Generic Structs (0)" not in report[which]


def test_the_two_implementations_still_agree(report):
    py_out, tool_out = report
    assert py_out.endswith(tool_out)


def test_they_agree_without_the_docs_too(built):
    slib, tool, _metadata = built
    tool_run = _run([str(tool), str(slib)])
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr
    env = dict(os.environ)
    env.pop("SUSHI_TOOLCHAIN_BIN", None)
    env["SUSHI_TOOLCHAIN"] = "off"
    py_run = _run(["sushic", "--lib-info", str(slib)], env=env)
    assert py_run.returncode == 0, py_run.stdout + py_run.stderr
    assert py_run.stdout.endswith(tool_run.stdout)
