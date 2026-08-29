"""Gate: only the backend and the stdlib generators may import llvmlite.

`semantics` must never learn what an LLVM type is. The layering invariant already
forbids `semantics` importing `backend`; this is the same rule one level down, for the
library the backend is built on. See IR.md Phase 0.
"""
from __future__ import annotations

import re
from pathlib import Path

SUSHI_LANG = Path(__file__).resolve().parents[2] / "sushi_lang"

# The two places that legitimately produce LLVM IR.
ALLOWED_ROOTS = (
    SUSHI_LANG / "backend",
    SUSHI_LANG / "sushi_stdlib",
)

_IMPORT = re.compile(r"^\s*(?:from\s+llvmlite|import\s+llvmlite)", re.MULTILINE)


def _is_allowed(path: Path) -> bool:
    return any(root in path.parents for root in ALLOWED_ROOTS)


def test_llvmlite_is_confined_to_the_backend_and_the_stdlib() -> None:
    offenders = []
    for path in SUSHI_LANG.rglob("*.py"):
        if "__pycache__" in path.parts or _is_allowed(path):
            continue
        if _IMPORT.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(SUSHI_LANG.parent)))

    assert not offenders, (
        "these modules import llvmlite outside backend/ and sushi_stdlib/:\n  "
        + "\n  ".join(sorted(offenders))
    )
