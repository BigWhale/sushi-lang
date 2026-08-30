"""Parity: `sushic --lib-info` and the toolchain slib-info tool print the same body.

The Python fallback (print_library_info) and the Sushi tool
(toolchain/src/slib_info.sushi) must render identical output for the same
.slib. The delegation seam in cli.py must run the tool when SUSHI_TOOLCHAIN_BIN
names it, skip it under SUSHI_TOOLCHAIN=off, and propagate the tool's exit code.
"""
from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path

import pytest
from sushic_path import SUSHIC, SUSHIC_AVAILABLE

REPO = Path(__file__).parents[2]
TOOL_SRC = REPO / "toolchain" / "src" / "slib_info.sushi"

# One declaration per --lib-info section: concrete functions with every mode
# family, a generic template, a constant, a struct, a payload and a unit enum
# variant, and one stdlib dependency.
DEMO_LIB = """\
use <collections/strings>

public const i32 ANSWER = 42

public struct Point:
    i32 x
    i32 y

public enum Shade:
    Plain()
    Custom(i32)

public fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)

public fn shout(nom string s) string:
    return Result.Ok(s)

public fn identity@(T)(nom T x) T:
    return Result.Ok(x)
"""


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _env(**overrides):
    env = dict(os.environ)
    env.pop("SUSHI_TOOLCHAIN", None)
    env.pop("SUSHI_TOOLCHAIN_BIN", None)
    env.update(overrides)
    return env


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Build the demo .slib and compile the slib-info tool once."""
    if not SUSHIC_AVAILABLE:
        pytest.skip("no compiler driver in this checkout")
    tmp = tmp_path_factory.mktemp("slibinfo")
    src = tmp / "demolib.sushi"
    src.write_text(DEMO_LIB, encoding="utf-8")
    slib = tmp / "demolib.slib"
    r = _run([SUSHIC, "--lib", "--lib-version", "1.2.3", str(src), "-o", str(slib)], cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    tool = tmp / "slib-info"
    r = _run([SUSHIC, str(TOOL_SRC), "-o", str(tool)], cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp, slib, tool


def test_python_fallback_matches_the_tool(built):
    _tmp, slib, tool = built
    tool_run = _run([str(tool), str(slib)])
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr
    assert tool_run.stdout != ""

    py_run = _run([SUSHIC, "--lib-info", str(slib)], env=_env(SUSHI_TOOLCHAIN="off"))
    assert py_run.returncode == 0, py_run.stdout + py_run.stderr
    # The Python path prints the banner first; the body must be identical.
    assert py_run.stdout.endswith(tool_run.stdout)


def test_the_tool_renders_every_section(built):
    _tmp, slib, tool = built
    out = _run([str(tool), str(slib)]).stdout
    assert "Library: demolib" in out
    assert "fn add(i32 a, i32 b) i32" in out
    assert "fn identity@(T)(nom T x) T" in out
    assert "const i32 ANSWER" in out
    assert "struct Point:" in out
    assert "enum Shade:" in out
    assert "Custom(i32)" in out
    assert "<collections/strings>" in out
    assert "Source: " in out


def _requires_compiler() -> str:
    from sushi_lang.internals.semver import Version, default_compiler_req
    from sushi_lang.internals.version import _get_versions

    return default_compiler_req(Version.parse(_get_versions()["app"]))


def test_a_source_report_carries_the_v4_fields(built):
    """kind, library_version, requires_compiler and the unit index all show."""
    _tmp, slib, tool = built
    out = _run([str(tool), str(slib)]).stdout
    assert "Version: 1.2.3" in out
    assert "Kind: source" in out
    assert f"Requires compiler: {_requires_compiler()}" in out
    assert "Units (1):" in out
    assert "\n  demolib\n" in out


def test_a_source_report_suppresses_the_binary_only_fields(built):
    """A source library has no platform and no bitcode, so neither line prints."""
    _tmp, slib, tool = built
    out = _run([str(tool), str(slib)]).stdout
    assert "Platform:" not in out
    assert "Bitcode:" not in out


@pytest.fixture(scope="module")
def built_binary(built, tmp_path_factory):
    """The same declarations, shipped the opt-in binary way."""
    _tmp, _slib, tool = built
    tmp = tmp_path_factory.mktemp("slibinfobin")
    src = tmp / "binlib.sushi"
    src.write_text(DEMO_LIB, encoding="utf-8")
    slib = tmp / "binlib.slib"
    r = _run([SUSHIC, "--lib", "--lib-kind", "binary", "--lib-version", "1.2.3",
              str(src), "-o", str(slib)], cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp, slib, tool


def test_a_binary_report_keeps_platform_and_bitcode(built_binary):
    _tmp, slib, tool = built_binary
    out = _run([str(tool), str(slib)]).stdout
    assert "Kind: binary" in out
    assert "Platform: " in out
    assert "Bitcode: " in out
    assert "Source:" not in out


def test_the_fallback_matches_the_tool_on_a_binary_library(built_binary):
    _tmp, slib, tool = built_binary
    tool_run = _run([str(tool), str(slib)])
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr
    py_run = _run([SUSHIC, "--lib-info", str(slib)], env=_env(SUSHI_TOOLCHAIN="off"))
    assert py_run.returncode == 0, py_run.stdout + py_run.stderr
    assert py_run.stdout.endswith(tool_run.stdout)


@pytest.mark.parametrize("surgery,label", [
    (lambda b: b.__setitem__(0, 0x00), "magic"),
    (lambda b: b.__setitem__(slice(16, 20), struct.pack("<I", 2)), "version"),
])
def test_a_corrupt_slib_exits_2(built, tmp_path, surgery, label):
    _tmp, slib, tool = built
    blob = bytearray(slib.read_bytes())
    surgery(blob)
    bad = tmp_path / f"bad_{label}.slib"
    bad.write_bytes(bytes(blob))
    r = _run([str(tool), str(bad)])
    assert r.returncode == 2, r.stdout + r.stderr


def test_usage_without_arguments_exits_2(built):
    _tmp, _slib, tool = built
    r = _run([str(tool)])
    assert r.returncode == 2


@pytest.fixture()
def stub_bin(tmp_path):
    """A stand-in tool that prints a sentinel and exits 3."""
    bin_dir = tmp_path / "stub_bin"
    bin_dir.mkdir()
    stub = bin_dir / "slib-info"
    stub.write_text("#!/bin/sh\necho SENTINEL-TOOL-RAN $1\nexit 3\n")
    stub.chmod(0o755)
    return bin_dir


def test_lib_info_delegates_to_the_toolchain_binary(built, stub_bin):
    _tmp, slib, _tool = built
    r = _run([SUSHIC, "--lib-info", str(slib)],
             env=_env(SUSHI_TOOLCHAIN_BIN=str(stub_bin)))
    assert "SENTINEL-TOOL-RAN" in r.stdout
    assert r.returncode == 3


def test_sushi_toolchain_off_skips_the_binary(built, stub_bin):
    _tmp, slib, _tool = built
    r = _run([SUSHIC, "--lib-info", str(slib)],
             env=_env(SUSHI_TOOLCHAIN="off", SUSHI_TOOLCHAIN_BIN=str(stub_bin)))
    assert "SENTINEL-TOOL-RAN" not in r.stdout
    assert r.returncode == 0, r.stdout + r.stderr
