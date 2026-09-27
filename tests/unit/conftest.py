"""Shared hooks for the unit-test layer. No test here runs the Sushi compiler; the test
runner's own tests, under `runner/`, are the one exception, and `compiler_stage_gate`
fails any other test that reaches a compiler stage (#959)."""
from __future__ import annotations

import sys
from pathlib import Path

UNIT_ROOT = Path(__file__).resolve().parent
if str(UNIT_ROOT) not in sys.path:
    sys.path.insert(0, str(UNIT_ROOT))

import compiler_stage_gate  # noqa: E402


def pytest_configure(config):
    config.pluginmanager.register(compiler_stage_gate, "compiler_stage_gate")
    compiler_stage_gate.configure(config, UNIT_ROOT)


def pytest_collection_modifyitems(items):
    """Mark every test in a subprocess-spawning module `slow`.

    Derived rather than hand-applied: the modules that qualify, and a marker maintained by
    hand across that many files is one a new E2E module forgets. Importing `subprocess` is
    what makes a module slow here -- it means sushic, clang or the packager CLI is spawned.
    """
    for item in items:
        module = getattr(item, "module", None)
        if module is not None and hasattr(module, "subprocess"):
            item.add_marker("slow")
