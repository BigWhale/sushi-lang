"""The gate on #478's "what a readable count costs".

llvmlite does not fold: `builder.add` of two constants emits `add i32 3, 4` into the module,
and at `--opt none` there is no second chance. So a readable range must be turned into values
by the FRONT end, and the three tiers are a rule rather than a hope about the optimizer.

Without this gate the tiers decay into "LLVM will fix it", which is false.
"""
from __future__ import annotations

import subprocess

from sushic_path import SUSHIC, needs_sushic

pytestmark = needs_sushic


def _emit(tmp_path, source: str) -> str:
    """The emitted module, at `--opt none`.

    The tiers are about what the FRONT end emits. An optimizer would fold the difference
    away and the gate would pass on a module that pays for the run-time mechanism, which is
    the thing being ruled out.
    """
    (tmp_path / "main.sushi").write_text(source, encoding="utf-8")
    built = subprocess.run([SUSHIC, "--write-ll", "--opt", "none", "main.sushi", "-o", "out"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert built.returncode in (0, 1), built.stdout + built.stderr
    return (tmp_path / "out.ll").read_text(encoding="utf-8")


def _fill_body(tmp_path, literal: str) -> str:
    """The module for a program whose only array is this literal."""
    return _emit(tmp_path, f"""fn main() i32:
    let i32[] a = {literal}
    return Result.Ok(a.len())
""")


def test_a_short_readable_range_emits_no_arithmetic_and_no_loop(tmp_path):
    """Tier 1: `[0..5]` is five stores of literals -- what `[0, 1, 2, 3, 4]` emits."""
    body = _fill_body(tmp_path, "from([0..5])")
    # Scoped to the FILL. The allocation legitimately multiplies a capacity by an element
    # size, so a bare "no mul in the module" would fail for the wrong reason.
    assert "run0_offset" not in body, "a short readable range emitted the walk's multiply"
    assert "run0_value" not in body, "a short readable range computed a value"
    assert "run0_range" not in body, "a short readable range emitted a walk"
    assert body.count("store i32 ") >= 5, "the five literal stores are missing"


def test_a_long_readable_range_walks_a_constant_trip_count(tmp_path):
    """Tier 2: above UNROLL_LIMIT the loop is what stops the IR growing with N."""
    body = _fill_body(tmp_path, "from([0..40])")
    assert "run0_range" in body, "a long readable range did not emit a walk"
    assert body.count("store i32") < 40, "a long readable range unrolled into stores"


def test_an_unreadable_range_computes_first_step_and_count(tmp_path):
    """Tier 3: the same walk, with everything computed. `select` is the direction."""
    ll = _emit(tmp_path, """fn slots(i32 n) i32[]:
    return Result.Ok(from([0..n]))

fn main() i32:
    let i32[] a = slots(5)??
    return Result.Ok(a.len())
""")
    assert "range_step" in ll, "the run-time direction select was not emitted"
    assert "range_magnitude" in ll, "the run-time count was not emitted"


def test_a_short_readable_repeat_still_emits_stores(tmp_path):
    """The repeated value keeps its own tiers, unchanged by the range work."""
    body = _fill_body(tmp_path, "from([7; 3])")
    assert "run0_fill" not in body, "a short repeat emitted a walk"
    assert body.count("store i32 7") == 3


def test_a_run_time_repeat_count_is_clamped(tmp_path):
    """A negative count cannot reach the walk, which compares with an unsigned predicate.

    The clamp removes that hazard by construction; RE2024 used to trap it instead.
    """
    ll = _emit(tmp_path, """fn zeros(i32 n) i32[]:
    return Result.Ok(from([0; n]))

fn main() i32:
    let i32[] a = zeros(3)??
    return Result.Ok(a.len())
""")
    assert "run_count" in ll, "a run-time repeat count carried no clamp"
    assert "RE2024" not in ll, "the retired trap is still emitted"


def test_a_constant_count_carries_no_clamp(tmp_path):
    """The clamp is emitted only where it can matter. A readable count is CE2017 if bad."""
    body = _fill_body(tmp_path, "from([7; 3])")
    assert "run_count" not in body, "a constant count paid for the clamp"
