"""R0.1 / W7: docs-vs-code stdlib smoke check."""
from __future__ import annotations

import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pytest
from sushic_path import SUSHIC, needs_sushic

# Documented stdlib surface -- one representative program per documented module.
# Each program is warning-free (no `??` in main, no unused bindings) so a clean
# compile is exit 0. `doc` is the docs/stdlib/ file the case is drawn from; the
# test asserts it exists, tying the check to the documentation it guards.
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
    let bool there = exists("/nonexistent_r0_smoke")
    println("exists {there}")
    return Result.Ok(0)
""",
    ),
    # `File`, `open()` and the console handles live in <io/fs> since HANDLES.md Phase 5;
    # `<io/stdio>` was retired with them, and console.md documents <io/fs> now.
    Case(
        "io/fs",
        "io/fs.md",
        """use <io/fs>
fn main() i32:
    match open("/nonexistent_r0_smoke", FileMode.Read()):
        Result.Ok(f) ->
            println("opened {f.is_open()}")
        Result.Err(_) ->
            println("io/fs err path")
    return Result.Ok(0)
""",
    ),
    Case(
        "io/fs console",
        "io/console.md",
        """use <io/fs>
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
        "collections/iter-methods",
        "collections/iter.md",
        """use <collections/iter>
fn main() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    xs.push(2)
    let List@(i32) ys = xs.map(|i32 x| x + 1).realise(List.new())
    println("iter methods {ys.len()}")
    return Result.Ok(0)
""",
    ),
    Case(
        "net/socket",
        "net/socket.md",
        """use <net/socket>

fn main() i32:
    match sock_tcp_listen("127.0.0.1", 0, 8):
        Result.Ok(fd) ->
            println("port {sock_local_port(fd).realise(-1)}")
            sock_close(fd).realise(-1)
        Result.Err(_) -> println("no listener")
    return Result.Ok(0)
""",
    ),
    Case(
        "net/tcp",
        "net/tcp.md",
        """use <net/tcp>

fn run() ~ | NetError:
    let TcpListener l = tcp_listen("127.0.0.1", 0, 8)??
    let i32 port = l.local_port()??
    println("port {port}")
    l.close()??
    return Result.Ok(~)

fn main() i32:
    match run():
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
""",
    ),
    Case(
        "net/udp",
        "net/udp.md",
        """use <net/udp>

fn run() ~ | NetError:
    let UdpSocket s = udp_bind("127.0.0.1", 0)??
    let i32 port = s.local_port()??
    println("port {port}")
    s.close()??
    return Result.Ok(~)

fn main() i32:
    match run():
        Result.Ok(_) -> return Result.Ok(0)
        Result.Err(_) -> return Result.Ok(1)
""",
    ),
    Case(
        "net/dns",
        "net/dns.md",
        """use <net/dns>
use <net/ip>

fn main() i32:
    match resolve("127.0.0.1"):
        Result.Ok(addresses) ->
            foreach(a in addresses.iter()):
                println("{a.text()}")
        Result.Err(_) -> println("no answer")
    return Result.Ok(0)
""",
    ),
    Case(
        "net/ip",
        "net/ip.md",
        """use <net/ip>

fn main() i32:
    match parse_ip("2001:db8::1"):
        Result.Ok(a) -> println("{a.text()}")
        Result.Err(_) -> println("not an address")
    return Result.Ok(0)
""",
    ),
    Case(
        "net/url",
        "net/url.md",
        """use <net/url>

fn main() i32:
    match parse_url("https://omakase.lubica.net/api?q=1"):
        Result.Ok(u) -> println("{u.scheme} {u.host} {u.path}")
        Result.Err(e) -> println("{e.text()}")
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


@pytest.fixture(scope="session")
def platform_stdlib():
    """Build the standard library for the current platform once per session."""
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
SEMANTIC_LAYER_SKIP = {
    "collections/hashmap", "collections/iter",
    "collections/iter-methods",
    # The bundled Sushi-source net modules. They are injected as compilation
    # units by compiler/pipeline.py, which the semantic layer does not do, so
    # the compile layer below is what covers them.
    "net/tcp", "net/udp", "net/dns", "net/ip", "net/url",
    # io/fs joined them in HANDLES.md Phase 5: File, open() and the console
    # handles are Sushi source now, not compiler builtins.
    "io/fs", "io/fs console",
}


def test_docs_present():
    """Every case is drawn from a docs/stdlib/ file that must exist."""
    missing = [c.id for c in CASES if not (DOCS_ROOT / c.doc).is_file()]
    assert not missing, f"documented stdlib pages missing under {DOCS_ROOT}: {missing}"


def test_semantic_layer_skips_are_covered_by_the_compile_layer():
    """A module excused from the semantic layer must still be a case the compile layer runs.

    The excuse for skipping these two is "the compile layer below covers them". That claim
    is only true while they remain in CASES -- drop one from CASES and the skip silently
    becomes zero coverage rather than one layer of it.
    """
    orphaned = SEMANTIC_LAYER_SKIP - set(CASE_IDS)
    assert not orphaned, (
        f"{sorted(orphaned)} are excused from the semantic layer but are not compile-layer "
        f"cases either, so nothing covers them. Remove them from SEMANTIC_LAYER_SKIP or "
        f"restore them to CASES."
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_documented_module_resolves(case, analyze):
    """Semantic layer: the documented program resolves with no semantic errors."""
    if case.id in SEMANTIC_LAYER_SKIP:
        pytest.skip(f"{case.id} resolves only in the full pipeline (covered by the compile layer)")
    reporter = analyze(case.source)
    errors = [d for d in reporter.items if getattr(d, "kind", None) == "error"]
    assert not errors, (
        f"[{case.id}] documented in docs/stdlib/{case.doc} produced semantic "
        f"error(s): {[getattr(d, 'code', '?') for d in errors]}"
    )


@needs_sushic
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_documented_module_compiles(case, tmp_path, platform_stdlib):
    """End-to-end layer: the documented program compiles and links (exit 0)."""
    (tmp_path / "main.sushi").write_text(case.source, encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, "main.sushi", "-o", "out"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"[{case.id}] documented in docs/stdlib/{case.doc} failed to compile "
        f"(exit {result.returncode}).\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
