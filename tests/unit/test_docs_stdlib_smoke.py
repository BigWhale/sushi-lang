"""R0.1 / W7: docs-vs-code stdlib smoke check.

Enumerates the documented standard-library surface (``docs/stdlib/``) and asserts
that each representative documented symbol still compiles in a minimal program.
The point is to catch documentation drift automatically: a documented module or
symbol that has been renamed, removed, or no longer type-checks fails here instead
of silently rotting in the docs.

Two layers are checked, per the R0 plan:

  * ``sushic`` subprocess compile -- the authoritative end-to-end check. It runs
    the whole pipeline (front-end + backend/``.bc`` linking), so it catches both a
    renamed/removed symbol and backend drift (a missing precompiled unit). This is
    the layer that satisfies W7's exit criterion.
  * in-process ``analyze`` fixture -- a fast semantic-resolution check. When it and
    the subprocess layer disagree, the failure is localized to the front-end vs. the
    backend.

Scope (v1, deliberately narrow): one representative documented symbol per documented
stdlib module, plus the built-in ``List``/array/``Maybe``/``Result`` surface. A full
grammar/method audit is out of scope. ``docs/stdlib/platform.md`` is excluded -- it is
a compiler-internals doc, not a user-facing module.

If a documented symbol legitimately stops compiling and the fix belongs to a later
phase, mark that case ``xfail`` with the tracking issue in the reason (mirroring the
R0.2 quarantine registry), so the suite stays green while the drift is tracked.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# Documented stdlib surface -- one representative program per documented module.
# Each program is warning-free (no `??` in main, no unused bindings) so a clean
# compile is exit 0. `doc` is the docs/stdlib/ file the case is drawn from; the
# test asserts it exists, tying the check to the documentation it guards.
# --------------------------------------------------------------------------- #
Case = namedtuple("Case", ["id", "doc", "source"])

CASES = [
    Case(
        "time",
        "time.md",
        """use <time>
fn main() i32:
    let i32 r = msleep(0 as i64).realise(-1)
    println("time {r}")
    return Result.Ok(0)
""",
    ),
    Case(
        "math",
        "math.md",
        """use <math>
fn main() i32:
    let f64 r = sqrt(16.0)
    let f64 p = PI
    let i32 a = abs(-42)
    println("math {r} {p} {a}")
    return Result.Ok(0)
""",
    ),
    Case(
        "random",
        "random.md",
        """use <random>
fn main() i32:
    srand(42 as u64)
    let i32 d = rand_range(1, 7)
    let u64 v = rand()
    println("random {d} {v}")
    return Result.Ok(0)
""",
    ),
    Case(
        "sys/env",
        "env.md",
        """use <sys/env>
fn main() i32:
    let string h = getenv("R0_DOES_NOT_EXIST").realise("none")
    println("env {h}")
    return Result.Ok(0)
""",
    ),
    Case(
        "sys/process",
        "process.md",
        """use <sys/process>
fn main() i32:
    let i32 pid = getpid()
    let i32 uid = getuid()
    println("process {pid} {uid}")
    return Result.Ok(0)
""",
    ),
    Case(
        "io/files",
        "io/files.md",
        """use <io/files>
fn main() i32:
    match open("/nonexistent_r0_smoke", FileMode.Read()):
        FileResult.Ok(f) ->
            f.close()
        FileResult.Err(_) ->
            println("io/files err path")
    return Result.Ok(0)
""",
    ),
    Case(
        "io/stdio",
        "io/console.md",
        """use <io/stdio>
fn main() i32:
    let u8[] data = from([72 as u8, 105 as u8])
    stdout.write_bytes(data)
    println("")
    return Result.Ok(0)
""",
    ),
    Case(
        "collections/strings",
        "collections/strings.md",
        """use <collections/strings>
fn main() i32:
    let string s = "hello world"
    let string u = s.upper()
    let string[] parts = s.split(" ")
    println("strings {u} {parts.len()}")
    return Result.Ok(0)
""",
    ),
    Case(
        "collections/hashmap",
        "collections/hashmap.md",
        """use <collections/hashmap>
fn main() i32:
    let HashMap@(i32, string) m = HashMap.new()
    m.insert(1, "one")
    let string v = m.get(1).realise("none")
    println("hashmap {v}")
    m.free()
    return Result.Ok(0)
""",
    ),
    Case(
        "collections/list",
        "collections/list.md",
        """fn main() i32:
    let List@(i32) l = List.new()
    l.push(42)
    println("list {l.len()}")
    l.free()
    return Result.Ok(0)
""",
    ),
    Case(
        "collections/arrays",
        "collections/arrays.md",
        """fn main() i32:
    let i32[] a = from([1, 2, 3])
    println("arrays {a.len()}")
    return Result.Ok(0)
""",
    ),
    Case(
        "collections/iter",
        "collections/iter.md",
        """use <collections/iter>
fn main() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    xs.push(2)
    let List@(i32) ys = map(xs, |i32 x| x + 1).realise(List.new())
    println("iter {ys.len()}")
    return Result.Ok(0)
""",
    ),
    Case(
        "maybe",
        "maybe.md",
        """fn main() i32:
    let Maybe@(i32) m = Maybe.Some(42)
    let i32 v = m.realise(0)
    println("maybe {v} {m.is_some()}")
    return Result.Ok(0)
""",
    ),
    Case(
        "result",
        "result.md",
        """fn main() i32:
    let Result@(i32, StdError) r = Result.Ok(42)
    let i32 v = r.realise(0)
    println("result {v}")
    return Result.Ok(0)
""",
    ),
]

CASE_IDS = [c.id for c in CASES]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = PROJECT_ROOT / "docs" / "stdlib"
SUSHIC = shutil.which("sushic")


@pytest.fixture(scope="session")
def platform_stdlib():
    """Build the standard library for the current platform once per session.

    The compile layer links per-platform stdlib bitcode (`sushi_stdlib/dist/<platform>/`).
    Only the darwin bitcode is committed, and the CI pytest job does not run the stdlib
    build (unlike the enhanced runner / test-linux job, which call run_tests.build_stdlib).
    Without this, `sushic` on Linux falls back to the darwin io bitcode, whose stdio globals
    (`__stderrp`/`__stdinp`/`__stdoutp`) are macOS-only symbols and fail to link. Building
    here makes the end-to-end compile check correct on any platform. build.py is fast
    (<1s) and deterministic (does not dirty committed bitcode).
    """
    build = PROJECT_ROOT / "sushi_lang" / "sushi_stdlib" / "build.py"
    result = subprocess.run(
        [sys.executable, str(build)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.fail(
            "stdlib build (build.py) failed, cannot run the compile layer:\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return True

# Modules whose symbols only resolve in the *full* compilation pipeline, not in the
# in-process `analyze` fixture. `collections/hashmap` is a virtual unit: HashMap is
# registered as a generic provider by compiler/pipeline.py (generic_type_map), a step
# the semantics-only fixture does not replicate. `collections/iter` is a bundled
# Sushi-source module that compiler/pipeline.py injects as a compilation unit (its
# combinators are not otherwise in scope). Both are still fully covered by the
# authoritative subprocess compile layer below.
SEMANTIC_LAYER_SKIP = {"collections/hashmap", "collections/iter"}


def test_docs_present():
    """Every case is drawn from a docs/stdlib/ file that must exist.

    A deleted/renamed doc means the documented surface this check guards has moved
    -- surface it here rather than silently testing against nothing.
    """
    missing = [c.id for c in CASES if not (DOCS_ROOT / c.doc).is_file()]
    assert not missing, f"documented stdlib pages missing under {DOCS_ROOT}: {missing}"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_documented_module_resolves(case, analyze):
    """Semantic layer: the documented program resolves with no semantic errors.

    Fast, in-process, front-end only (parser + passes). Localizes a failure to the
    front-end when the subprocess layer also fails.
    """
    if case.id in SEMANTIC_LAYER_SKIP:
        pytest.skip(f"{case.id} resolves only in the full pipeline (covered by the compile layer)")
    reporter = analyze(case.source)
    errors = [d for d in reporter.items if getattr(d, "kind", None) == "error"]
    assert not errors, (
        f"[{case.id}] documented in docs/stdlib/{case.doc} produced semantic "
        f"error(s): {[getattr(d, 'code', '?') for d in errors]}"
    )


@pytest.mark.skipif(SUSHIC is None, reason="sushic not on PATH (run under `uv run pytest`)")
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_documented_module_compiles(case, tmp_path, platform_stdlib):
    """End-to-end layer: the documented program compiles and links (exit 0).

    Authoritative docs-vs-code check -- exercises the full pipeline including
    backend/``.bc`` linking, so a missing precompiled stdlib unit is caught here.
    Depends on `platform_stdlib` so the correct per-platform bitcode is linked.
    """
    (tmp_path / "main.sushi").write_text(case.source, encoding="utf-8")
    result = subprocess.run(
        ["sushic", "main.sushi", "-o", "out"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"[{case.id}] documented in docs/stdlib/{case.doc} failed to compile "
        f"(exit {result.returncode}).\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
