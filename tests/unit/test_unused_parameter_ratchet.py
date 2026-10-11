"""The count of unused parameters under `sushi_lang/` may only go down (#989).

vulture matches NAMES across the package, so a parameter nothing reads stays invisible to
the dead-code gate whenever any other code reads the same name. ruff's `ARG` rules see a
parameter inside its own function. Most of today's hits are the uniform signatures of
dispatch-table emitters, which must take what the table passes, so the rule cannot be on
for the whole lint scope and a per-file ignore is not allowed. This scan counts the hits
instead: a new unused parameter fails it, and a removed one must lower `ARG_HITS`.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

ARG_HITS = 177


def unused_parameters() -> list[str]:
    """Every ruff `ARG` hit under `sushi_lang/`, as `path:line: message`."""
    if importlib.util.find_spec("ruff") is None:
        pytest.fail("ruff is not installed. It is in the `dev` extras: "
                    "run `uv sync --extra dev`, then `uv run pytest`.")
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "sushi_lang", "--select", "ARG",
         "--output-format", "json", "--no-cache", "--exit-zero"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        pytest.fail(f"ruff failed (exit {proc.returncode}):\n{proc.stderr}")
    return [f"{Path(hit['filename']).relative_to(ROOT)}:{hit['location']['row']}: "
            f"{hit['code']} {hit['message']}" for hit in json.loads(proc.stdout)]


def test_the_scan_sees_an_unused_parameter(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("def f(used, unused):\n    return used\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(probe), "--select", "ARG",
         "--output-format", "json", "--no-cache", "--exit-zero", "--isolated"],
        capture_output=True, text=True, timeout=60)
    assert [hit["code"] for hit in json.loads(proc.stdout)] == ["ARG001"]


def test_unused_parameters_only_go_down():
    hits = unused_parameters()
    assert len(hits) <= ARG_HITS, (
        f"{len(hits)} unused parameters, the ratchet allows {ARG_HITS}. Remove the new "
        "one; a parameter a dispatch table forces on the signature is the one "
        "exception, and it is not a new one. The hits:\n  " + "\n  ".join(hits))
    assert len(hits) >= ARG_HITS, (
        f"{len(hits)} unused parameters, below the ratchet: lower ARG_HITS to "
        f"{len(hits)}.")
