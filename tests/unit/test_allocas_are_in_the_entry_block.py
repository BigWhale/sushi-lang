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
from pathlib import Path


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





# Each program drives emitters that the issue named, and each puts them under a loop, so
# a slot at the current position is one this gate can see.




