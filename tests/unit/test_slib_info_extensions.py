"""The manifest records the foreign types a library claims methods on.

`foreign_extensions` is the consumer's half of CW3003: `--lib-info` shows the
claims before a second library makes them collide. The key is absent when the
library extends only what it declares.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
SUSHIC = REPO / "sushic"
TOOL_SRC = REPO / "toolchain" / "src" / "slib_info.sushi"

CLAIMING_LIB = """\
public struct Crate:
    i32 weight

extend Crate heavier() i32:
    return self.weight + 1

extend i32 twice_over() i32:
    return self * 2

extend string shouted() i32:
    return self.len()
"""

QUIET_LIB = """\
public struct Crate:
    i32 weight

extend Crate heavier() i32:
    return self.weight + 1
"""


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _env(**overrides):
    env = dict(os.environ)
    env.pop("SUSHI_TOOLCHAIN", None)
    env.pop("SUSHI_TOOLCHAIN_BIN", None)
    env.update(overrides)
    return env


def _build_lib(tmp: Path, name: str, source: str) -> Path:
    src = tmp / f"{name}.sushi"
    src.write_text(source, encoding="utf-8")
    out = tmp / f"{name}.slib"
    r = _run([str(SUSHIC), "--lib", "--lib-version", "0.0.1",
              str(src), "-o", str(out)], cwd=tmp)
    assert r.returncode in (0, 1), r.stdout + r.stderr
    assert out.exists(), r.stdout + r.stderr
    return out


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("slibext")
    claiming = _build_lib(tmp, "claiming", CLAIMING_LIB)
    quiet = _build_lib(tmp, "quiet", QUIET_LIB)
    return tmp, claiming, quiet


def _manifest(slib: Path) -> dict:
    from sushi_lang.backend.library_format import LibraryFormat
    return LibraryFormat.read_metadata_only(slib)


def test_the_manifest_lists_every_foreign_claim(built):
    _tmp, claiming, _quiet = built
    records = _manifest(claiming).get("foreign_extensions")
    assert records == [
        {"type": "i32", "method": "twice_over", "unit": "claiming"},
        {"type": "string", "method": "shouted", "unit": "claiming"},
    ]


def test_an_own_type_claim_stays_out_of_the_manifest(built):
    _tmp, _claiming, quiet = built
    assert "foreign_extensions" not in _manifest(quiet)


def test_lib_info_prints_the_section(built):
    _tmp, claiming, _quiet = built
    r = _run([str(SUSHIC), "--lib-info", str(claiming)],
             env=_env(SUSHI_TOOLCHAIN="off"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Foreign Extensions (2):" in r.stdout
    assert "  extend i32 twice_over" in r.stdout
    assert "  extend string shouted" in r.stdout


def test_lib_info_hides_an_empty_section(built):
    _tmp, _claiming, quiet = built
    r = _run([str(SUSHIC), "--lib-info", str(quiet)],
             env=_env(SUSHI_TOOLCHAIN="off"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Foreign Extensions" not in r.stdout


def test_the_tool_matches_the_python_fallback(built):
    tmp, claiming, _quiet = built
    if shutil.which("sushic") is None and not SUSHIC.exists():
        pytest.skip("sushic not available")
    tool = tmp / "slib-info"
    r = _run([str(SUSHIC), str(TOOL_SRC), "-o", str(tool)], cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    tool_run = _run([str(tool), str(claiming)])
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr
    assert "Foreign Extensions (2):" in tool_run.stdout
    py_run = _run([str(SUSHIC), "--lib-info", str(claiming)],
                  env=_env(SUSHI_TOOLCHAIN="off"))
    assert py_run.stdout.endswith(tool_run.stdout)
