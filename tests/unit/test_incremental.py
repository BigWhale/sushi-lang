"""End-to-end tests for incremental-compilation cache (P0-5).

Drives the real compiler (``sushic``) over a real multi-unit project written
to a tmp_path directory and asserts cache behaviour via:
  - stdout markers: ``[rebuilt]`` / ``[cached]`` printed per unit
  - cache artefacts under ``<project>/__sushi_cache__/units/``
  - executable output (for the correctness-under-caching guard)

Constraints honoured:
  - No compiler/language source is modified.
  - All fixtures are stdlib-free (builtin println only; no ``use <...>``).
  - All fixtures are enum-free (issue #26: compute_unit_fingerprint crashes on
    enums in multi-unit builds).
  - Local imports use quoted path syntax: ``use "helper"``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compile(project_dir: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Invoke ``sushic main.sushi -o out`` in *project_dir* and return the result.

    The compiler is invoked via the ``sushic`` console-script entry point so
    that it is on PATH under ``uv run pytest`` without requiring ``chmod +x``.
    Stdout and stderr are captured; the CWD is set to the project directory so
    that the default cache (``__sushi_cache__/``) lands there.
    """
    cmd = ["sushic", "main.sushi", "-o", "out"]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(
        cmd,
        cwd=project_dir,
        capture_output=True,
        text=True,
    )


def _rebuilt(stdout: str) -> set[str]:
    """Return the set of unit names that were reported as ``[rebuilt]``."""
    result = set()
    for line in stdout.splitlines():
        if "[rebuilt]" in line:
            # Lines look like: "  helpers/math_helper            [rebuilt]"
            name = line.strip().split()[0]
            result.add(name)
    return result


def _cached(stdout: str) -> set[str]:
    """Return the set of unit names that were reported as ``[cached]``."""
    result = set()
    for line in stdout.splitlines():
        if "[cached]" in line:
            name = line.strip().split()[0]
            result.add(name)
    return result


def _write(path: Path, content: str) -> None:
    """Write *content* to *path*, creating parent directories as needed.
    Ensures a trailing newline (avoids Sushi compilation warning).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content if content.endswith("\n") else content + "\n"
    path.write_text(text, encoding="utf-8")


def _make_project(project_dir: Path) -> tuple[Path, Path]:
    """Create a minimal two-unit project under *project_dir*.

    Returns (main_path, helper_path).

    helper.sushi exports:
      - const BASE = 10
      - public fn doubled(i32 x) i32 -> x * 2

    main.sushi imports helper and prints doubled(21) == 42.
    """
    helper = project_dir / "helpers" / "helper.sushi"
    main = project_dir / "main.sushi"

    _write(helper, """\
const i32 BASE = 10

public fn doubled(i32 x) i32:
    return Result.Ok(x * 2)
""")
    _write(main, """\
use "helpers/helper"

fn main() i32:
    let i32 r = doubled(21).realise(0)
    println(r)
    return Result.Ok(0)
""")
    return main, helper


# ---------------------------------------------------------------------------
# Scenario 1 — Cold build
# ---------------------------------------------------------------------------

def test_cold_build_all_units_rebuilt(tmp_path):
    """Fresh project: every unit reports [rebuilt]; cache artefacts are created."""
    _make_project(tmp_path)
    result = _compile(tmp_path)

    assert result.returncode == 0, f"Compilation failed:\n{result.stderr}"
    assert _rebuilt(result.stdout) == {"main", "helpers/helper"}
    assert _cached(result.stdout) == set()

    # Cache artefacts must exist. An object is named for what produced it --
    # <unit>.<global-key>.<fingerprint>.o -- so match on the stem, not the whole name.
    cache_units = tmp_path / "__sushi_cache__" / "units"
    assert list(cache_units.glob("main.*.o"))
    assert list((cache_units / "helpers").glob("helper.*.o"))
    assert not list(cache_units.rglob("*.tmp"))

    # Executable is runnable and produces expected output
    exe = tmp_path / "out"
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    assert out.returncode == 0
    assert "42" in out.stdout


# ---------------------------------------------------------------------------
# Scenario 2 — No-op rebuild
# ---------------------------------------------------------------------------

def test_noop_rebuild_all_units_cached(tmp_path):
    """Second build with no source changes: every unit reports [cached]."""
    _make_project(tmp_path)
    first = _compile(tmp_path)
    assert first.returncode == 0

    second = _compile(tmp_path)
    assert second.returncode == 0
    assert _cached(second.stdout) == {"main", "helpers/helper"}
    assert _rebuilt(second.stdout) == set()


# ---------------------------------------------------------------------------
# Scenario 3 — Leaf-body change
# ---------------------------------------------------------------------------

def test_leaf_body_change_rebuilds_only_that_unit(tmp_path):
    """Editing the helper's function body rebuilds the helper; main stays cached."""
    main, helper = _make_project(tmp_path)
    first = _compile(tmp_path)
    assert first.returncode == 0

    # Change body only (same public signature)
    _write(helper, """\
const i32 BASE = 10

public fn doubled(i32 x) i32:
    return Result.Ok(x * 3)
""")

    second = _compile(tmp_path)
    assert second.returncode == 0
    assert "helpers/helper" in _rebuilt(second.stdout)
    assert "main" in _cached(second.stdout)


# ---------------------------------------------------------------------------
# Scenario 4 — Dependency signature change
# ---------------------------------------------------------------------------

def test_signature_change_rebuilds_dependent(tmp_path):
    """Adding a new public function to the helper invalidates main's fingerprint."""
    main, helper = _make_project(tmp_path)
    first = _compile(tmp_path)
    assert first.returncode == 0

    # Add a new public function — main's DEP_SYMBOLS fingerprint component changes
    _write(helper, """\
const i32 BASE = 10

public fn doubled(i32 x) i32:
    return Result.Ok(x * 2)

public fn tripled(i32 x) i32:
    return Result.Ok(x * 3)
""")

    second = _compile(tmp_path)
    assert second.returncode == 0
    # Both units rebuild: helper because its source changed, main because its
    # dependency's public symbols changed (DEP_SYMBOLS component of fingerprint).
    assert "helpers/helper" in _rebuilt(second.stdout)
    assert "main" in _rebuilt(second.stdout)


# ---------------------------------------------------------------------------
# Scenario 5 — Comment/whitespace-only change
# ---------------------------------------------------------------------------

def test_whitespace_only_change_rebuilds_unit(tmp_path):
    """Whitespace-only change rebuilds that unit (fingerprint hashes raw source bytes).

    This documents current behaviour: the fingerprint includes raw source bytes,
    so even a trailing-newline addition or comment change forces a rebuild.
    """
    main, helper = _make_project(tmp_path)
    first = _compile(tmp_path)
    assert first.returncode == 0

    # Append a blank comment line — semantics are identical, bytes differ
    original = helper.read_text(encoding="utf-8")
    _write(helper, original + "\n# a comment that changes nothing\n")

    second = _compile(tmp_path)
    assert second.returncode == 0
    assert "helpers/helper" in _rebuilt(second.stdout)
    # main is unchanged and its dependency's PUBLIC SYMBOLS didn't change,
    # so main should remain cached (comment doesn't affect public_symbols).
    assert "main" in _cached(second.stdout)


# ---------------------------------------------------------------------------
# Scenario 6 — Global-parameter change (opt level)
# ---------------------------------------------------------------------------

def test_opt_level_change_invalidates_entire_cache(tmp_path):
    """Changing --opt level rebuilds every unit."""
    _make_project(tmp_path)
    first = _compile(tmp_path, ["--opt", "mem2reg"])
    assert first.returncode == 0
    assert _rebuilt(first.stdout) == {"main", "helpers/helper"}

    # Recompile with a different opt level. The opt level is part of every object's
    # cache key, so none of them can be hit -- a miss, not a wipe (the wipe raced
    # concurrent compilers; see cache.py and issue #196).
    second = _compile(tmp_path, ["--opt", "O2"])
    assert second.returncode == 0
    assert _rebuilt(second.stdout) == {"main", "helpers/helper"}
    assert _cached(second.stdout) == set()


# ---------------------------------------------------------------------------
# Scenario 7 — Correctness under caching (stale-cache false-hit guard)
# ---------------------------------------------------------------------------

def test_correctness_under_caching_no_stale_output(tmp_path):
    """Critical false-hit guard: a rebuild after a leaf change must reflect new code.

    If the cache ever incorrectly reuses stale object code, the output B would
    still equal A even though the source was changed.
    """
    main, helper = _make_project(tmp_path)

    # Cold build — doubled(21) == 42
    first_compile = _compile(tmp_path)
    assert first_compile.returncode == 0
    exe = tmp_path / "out"
    out_a = subprocess.run([str(exe)], capture_output=True, text=True)
    assert out_a.returncode == 0
    output_a = out_a.stdout.strip()
    assert output_a == "42", f"Expected '42', got {output_a!r}"

    # Edit the helper to return x * 3 instead of x * 2 — doubled(21) should now == 63
    _write(helper, """\
const i32 BASE = 10

public fn doubled(i32 x) i32:
    return Result.Ok(x * 3)
""")

    second_compile = _compile(tmp_path)
    assert second_compile.returncode == 0, f"Recompilation failed:\n{second_compile.stderr}"
    # Verify the helper was actually rebuilt (not stale-cached)
    assert "helpers/helper" in _rebuilt(second_compile.stdout), (
        "Expected helpers/helper to be rebuilt after body change"
    )

    out_b = subprocess.run([str(exe)], capture_output=True, text=True)
    assert out_b.returncode == 0
    output_b = out_b.stdout.strip()
    assert output_b == "63", (
        f"Expected '63' (new computation), got {output_b!r}. "
        "Cache may be serving stale object code."
    )
    assert output_b != output_a, "Output did not change after source edit — stale cache hit?"


# ---------------------------------------------------------------------------
# Scenario 8 — --no-incremental forces full rebuild
# ---------------------------------------------------------------------------

def test_no_incremental_flag_forces_full_rebuild(tmp_path):
    """--no-incremental bypasses cache; every unit is rebuilt even with no source change."""
    _make_project(tmp_path)
    first = _compile(tmp_path)
    assert first.returncode == 0
    assert _rebuilt(first.stdout) == {"main", "helpers/helper"}

    second = _compile(tmp_path, ["--no-incremental"])
    assert second.returncode == 0
    # When --no-incremental is set, the pipeline falls back to monolithic mode
    # which does not print [cached]/[rebuilt] per-unit.  Verify: no [cached] lines.
    assert _cached(second.stdout) == set()
