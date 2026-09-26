"""A stdlib platform module has one name, and the loader changes no `sys.path` (#904).

The probe runs in a subprocess: a module another test imported first must not hide a
second name or a `sys.path` change that the loader makes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = r"""
import importlib, json, pkgutil, sys
path_before = list(sys.path)
import sushi_lang.sushi_stdlib.src as src
from sushi_lang.backend.platform_detect import get_current_platform
from sushi_lang.sushi_stdlib.src._platform import get_platform_module
import sushi_lang.sushi_stdlib.src._platform as platform_pkg

generators = []
for info in pkgutil.walk_packages(src.__path__, src.__name__ + "."):
    if "._platform" in info.name:
        continue
    module = importlib.import_module(info.name)
    if hasattr(module, "generate_module_ir"):
        module.generate_module_ir()
        generators.append(info.name)

plat = get_current_platform()
os_name = "darwin" if plat.is_darwin else "linux"
prefix = f"{platform_pkg.__name__}.{os_name}"
leaves = [i.name for i in pkgutil.iter_modules(importlib.import_module(prefix).__path__)]
mismatched = []
for leaf in leaves:
    got = get_platform_module(leaf)
    want = importlib.import_module(f"{prefix}.{leaf}")
    if got is not want:
        mismatched.append(f"{leaf}: {got.__name__}")

print(json.dumps({
    "generators": generators,
    "leaves": leaves,
    "mismatched": mismatched,
    "short_names": sorted(k for k in sys.modules if k.split(".")[0] == "sushi_stdlib"),
    "path_added": [p for p in sys.path if p not in path_before],
}))
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_probe_reaches_the_generators_and_the_leaves(probe):
    assert len(probe["generators"]) >= 8, probe["generators"]
    assert "files" in probe["leaves"], probe["leaves"]


def test_no_module_is_loaded_under_the_short_name(probe):
    assert probe["short_names"] == []


def test_the_loader_does_not_change_sys_path(probe):
    assert probe["path_added"] == []


def test_the_loader_answers_the_module_of_the_full_name(probe):
    assert probe["mismatched"] == []


def test_an_unsupported_platform_is_one_clear_error(monkeypatch):
    import sushi_lang.sushi_stdlib.src._platform as platform_pkg

    other = SimpleNamespace(is_darwin=False, is_linux=False, is_windows=True, os="windows")
    monkeypatch.setattr(platform_pkg, "get_current_platform", lambda: other)
    with pytest.raises(RuntimeError, match="Unsupported platform: windows"):
        platform_pkg.get_platform_module("files")
