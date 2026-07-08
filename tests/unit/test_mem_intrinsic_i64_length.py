"""Guardrail for issues #149 / #151 / #152: ``llvm.memcpy`` / ``llvm.memmove`` /
``llvm.memset`` intrinsics must be declared with an ``i64`` (size_t) length, never
``i32``.

Background: a ``string`` is a 3-field fat pointer ``{i8* data, i32 size, i8 owned}``
(see ``docs/design/string-representation.md``). Passing that raw ``i32`` size field --
which sits next to the ``owned`` byte and padding -- as a ``mem*`` length lets garbage
upper bits reach glibc's SIMD routines on x86-64, producing an out-of-bounds access
(#149). The fix is the ``i64``-length intrinsic with the size zero-extended at the call
site (#149/#151). The 3-field representation is deliberate (#152 closed), so the
recurrence surface is guarded here instead: this test fails if a new ``i32``-length
``mem*`` intrinsic is introduced.

(The ``memcmp`` extern length is covered separately by the ``RESERVED_EXTERNS`` sync in
``sushi_lang/backend/runtime/core.py``.)
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "sushi_lang"

# Match: declare_intrinsic('llvm.memcpy'|'llvm.memmove'|'llvm.memset', [ <types> ])
# DOTALL so the multi-line argument-list form is captured too.
_DECL_RE = re.compile(
    r"declare_intrinsic\(\s*['\"]llvm\.(memcpy|memmove|memset)['\"]\s*,\s*\[(.*?)\]",
    re.DOTALL,
)


def _split_top_level(arglist: str) -> list[str]:
    """Split a comma-separated argument list on top-level commas only (respecting
    parentheses/brackets), so ``ir.PointerType(i8)`` is not split mid-call."""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in arglist:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur)
    if tail.strip():
        parts.append(tail)
    return [p.strip() for p in parts]


def _iter_py_files():
    for path in SRC_ROOT.rglob("*.py"):
        if ".bak" in path.name:
            continue
        yield path


def test_mem_intrinsics_declare_i64_length():
    violations: list[str] = []
    for path in _iter_py_files():
        text = path.read_text(encoding="utf-8")
        for m in _DECL_RE.finditer(text):
            name = m.group(1)
            types = _split_top_level(m.group(2))
            if not types:
                continue
            length_ty = types[-1]  # mem* length is always the last declared type
            # Flag the i32 regression specifically (a copy-paste of the old form);
            # i64 forms include `i64`, `ir.IntType(64)`, and `ir.IntType(INT64_BIT_WIDTH)`.
            if re.search(r"\bi32\b|IntType\(32\)|INT32_BIT_WIDTH", length_ty):
                line = text[: m.start()].count("\n") + 1
                violations.append(
                    f"{path.relative_to(REPO_ROOT)}:{line}: "
                    f"llvm.{name} declared with i32 length ({length_ty!r})"
                )

    assert not violations, (
        "llvm.mem* intrinsics must use an i64 (size_t) length; zero-extend the i32 "
        "size at the call site (issues #149/#151, docs/design/string-representation.md):\n"
        + "\n".join(violations)
    )
