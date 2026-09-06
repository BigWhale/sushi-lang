"""One place makes a stack slot, and it puts it in the ENTRY block (#589).

An `alloca` that a loop reaches runs again on every pass, and LLVM releases the frame
only at the return, so the stack grows without a limit until the program hits the guard
page. It is not an optimizer question either: `mem2reg` and SROA promote a slot in the
entry block and nowhere else, so a slot placed anywhere else stays in memory at every
`--opt` level.

Two gates, because one alone is not enough. The SOURCE gate keeps the seam the only
caller of `IRBuilder.alloca`. The IR gate reads what the compiler actually emitted, so a
new emitter that finds another way to the same mistake fails here as well.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

from sushic_path import SUSHIC, needs_sushic

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

#: Both IR producers: the backend, and the generators that emit the standard library.
EMITTERS = (SOURCE_ROOT / "backend", SOURCE_ROOT / "sushi_stdlib")

#: The one module that may call `IRBuilder.alloca`.
SEAM = SOURCE_ROOT / "backend" / "memory" / "allocas.py"


def _emitter_sources():
    for root in EMITTERS:
        for path in sorted(root.rglob("*.py")):
            yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_only_the_seam_calls_the_builder_alloca():
    """`builder.alloca(...)` outside the seam is a slot with no entry-block rule."""
    offenders: list[str] = []
    for path, tree in _emitter_sources():
        if path == SEAM:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "alloca":
                offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{node.lineno}")
    assert not offenders, (
        "a stack slot is made outside backend/memory/allocas.py:\n  "
        + "\n  ".join(offenders)
        + "\nRoute it through `codegen.memory.entry_alloca(...)`, or through "
        "`allocas.entry_alloca(builder, ...)` when the builder is not `codegen.builder`. "
        "A slot placed at the current position grows the frame on every pass of a loop "
        "that reaches it (#589)."
    )


# ---------------------------------------------------------------------------
# The IR gate.

_DEFINE = re.compile(r"^define\s.*?@\"?([\w.$]+)\"?\(")
_LABEL = re.compile(r"^([-\w.$]+):")


def _allocas_outside_entry(ir_text: str) -> list[str]:
    """Every `alloca` the module places outside the entry block of its function."""
    offenders: list[str] = []
    function = None
    entry = None
    block = None
    for line in ir_text.splitlines():
        define = _DEFINE.match(line)
        if define is not None:
            function, entry, block = define.group(1), None, None
            continue
        if function is None:
            continue
        if line.startswith("}"):
            function = None
            continue
        label = _LABEL.match(line)
        if label is not None:
            block = label.group(1)
            if entry is None:
                entry = block
            continue
        if " = alloca " in line and block != entry:
            offenders.append(f"@{function} {block}: {line.strip()}")
    return offenders


# Each program drives emitters that the issue named, and each puts them under a loop, so
# a slot at the current position is one this gate can see.
CORPUS = {
    "nested_range": """fn main() i32:
    let i32 total = 0
    foreach(i in 0..4):
        foreach(j in 0..2):
            total := total + j
    println("{total}")
    return Result.Ok(0)
""",
    "array_and_list": """use <collections/iter>

fn main() i32:
    let i32[] arr = from([3, 1, 2])
    let List@(i32) items = List.new()
    items.push(7)
    let i32 total = 0
    foreach(i in 0..3):
        foreach(n in arr.iter()):
            total := total + n
        foreach(n in items.iter()):
            total := total + n
        let i32[] copy = arr.clone()
        copy.reverse()
        total := total + copy.get(0).realise(0)
        let i32[] fresh = from([1, 2, 3])
        total := total + fresh.len()
    println("{total}")
    return Result.Ok(0)
""",
    "hashmap": """use <collections/hashmap>

fn main() i32:
    let HashMap@(i32, i32) m = HashMap.new()
    m.insert(1, 10)
    let i32 total = 0
    foreach(i in 0..3):
        m.insert(1, 10)
        total := total + m.get(1).realise(0)
        foreach(e in m.entries()):
            total := total + e.value
        foreach(k in m.keys()):
            total := total + k
    println("{total}")
    m.free()
    return Result.Ok(0)
""",
    "owning_elements": """struct Buf:
    i32[] data

fn main() i32:
    let i32 total = 0
    foreach(i in 0..3):
        let Buf[] bufs = from([Buf(data: from([1, 2])), Buf(data: from([3]))])
        let Buf[] copy = bufs.clone()
        total := total + copy.len()
    println("{total}")
    return Result.Ok(0)
""",
    "stdlib_calls": """use <time>
use <sys/env>
use <collections/strings>

fn main() i32:
    let i64 total = 0
    let i32 passes = 0
    while (passes < 3):
        total := total + now().realise(0)
        let string home = getenv("HOME").realise("none")
        total := total + (home.len() as i64)
        passes := passes + 1
    println("{total > 0}")
    return Result.Ok(0)
""",
}


def _ir(tmp_path, name: str, source: str) -> str:
    directory = tmp_path / name
    directory.mkdir()
    (directory / "main.sushi").write_text(source, encoding="utf-8")
    built = subprocess.run(
        [SUSHIC, "--write-ll", "--opt", "none", "--no-incremental",
         "main.sushi", "-o", "out"],
        cwd=directory, capture_output=True, text=True, timeout=300)
    assert built.returncode in (0, 1), built.stdout + built.stderr
    return (directory / "out.ll").read_text(encoding="utf-8")


@needs_sushic
def test_every_alloca_lands_in_the_entry_block(tmp_path):
    offenders: list[str] = []
    for name, source in CORPUS.items():
        for line in _allocas_outside_entry(_ir(tmp_path, name, source)):
            offenders.append(f"{name}: {line}")
    assert not offenders, (
        "a stack slot was emitted outside the entry block:\n  "
        + "\n  ".join(offenders)
        + "\nA loop that reaches one of these grows the frame on every pass (#589)."
    )
