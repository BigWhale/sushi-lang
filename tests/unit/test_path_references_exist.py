"""Every path-shaped reference in tracked documentation and comments names a real file.

The gate #366 asked for: a backtick-quoted `dir/file.py` reference that resolves to no
real file is a fact that has already drifted -- the seven references that issue lists
each named `semantics/passes/borrow.py` after #362 deleted it, and one of them was stale
the day it was written. A reference resolves if it exists from the repo root or a file
under the scanned roots ends with it (so `borrow/calls.py` matches
`sushi_lang/semantics/passes/borrow/calls.py`); an optional `:line` or `:symbol` suffix
is ignored. Bare filenames with no directory component are not asserted -- `borrow.py`
alone is ambiguous by design (issue #366).

The scan walks `docs/`, `sushi_lang/` and `tests/` on the FILESYSTEM -- deliberately not
`git ls-files`, so it needs no repository (and the local-only top-level notes such as
BUGS.md stay out of it by not scanning the repo root).
"""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PATH_REF = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*/[A-Za-z0-9_.-]+\.(?:py|sushi|lark|md))(?::[^`]*)?`"
)

SCAN_ROOTS = ("docs", "sushi_lang", "tests")
SKIP_DIRS = {"__pycache__", "__sushi_cache__", ".pytest_cache", "node_modules",
             ".venv", "venv", ".git", "site"}

# Files whose references are exempt. This module exempts itself for the same reason a
# linter does: its docstring must be able to NAME a broken reference as the example.
# (CHANGELOG.md sits at the repo root, outside the scanned roots -- a changelog entry
# is frozen history, correct for the tree at the release it records.)
FROZEN = {"tests/unit/test_path_references_exist.py"}

# References that do not name a real file ON PURPOSE. Each entry carries its reason;
# adding one is deliberate, which is the point of listing them here.
ALLOWED = {
    # Placeholders in how-to-extend documentation (docs/stdlib/platform.md and peers):
    # the path teaches where a NEW file would go.
    "darwin/myfeature.py",
    "linux/myfeature.py",
    "sushi_stdlib/src/_platform/posix/myfeature.py",
    "sushi_stdlib/src/myfeature.py",
    "sys/myfeature/functions.py",
    "tests/test_feature.sushi",
    # Deliberate historical references, each in a sentence that says the file is gone:
    # "There used to be a `backend/interfaces.py` ..." (architecture.md, Tier 4.5) and
    # "(The old `semantics/pipeline.py` scaffold ... deleted in Tier 3 ...)" (closures.md).
    "backend/interfaces.py",
    "semantics/pipeline.py",
    # TEMPORARY: the doc-block feature creates this module in its phase 2
    # (docs/design/documentation.md, section 12). Remove this entry when the
    # module exists.
    "internals/errors/docs.py",
    # TEMPORARY, same phase: sections 4 and 5 name the two gates that hold their
    # rulings true. Remove both entries when phase 2 writes them.
    "tests/unit/test_doc_block_attachment.py",
    "tests/unit/test_doc_block_grammar.py",
}


def walked_files() -> list[str]:
    found: list[str] = []
    for root in SCAN_ROOTS:
        for path in sorted((PROJECT_ROOT / root).rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(PROJECT_ROOT)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            found.append(str(rel))
    return found


def _resolves(ref: str, walked: list[str]) -> bool:
    return (PROJECT_ROOT / ref).exists() or any(p.endswith("/" + ref) for p in walked)


def test_path_references_resolve():
    walked = walked_files()

    unresolved: dict[str, list[str]] = {}
    for f in walked:
        if not f.endswith((".md", ".py", ".sushi")) or f in FROZEN:
            continue
        text = (PROJECT_ROOT / f).read_text(errors="replace")
        for m in PATH_REF.finditer(text):
            ref = m.group(1)
            if ref in ALLOWED or _resolves(ref, walked):
                continue
            unresolved.setdefault(ref, []).append(f)

    assert not unresolved, (
        "path-shaped references that resolve to no tracked file "
        "(fix the reference, or add it to ALLOWED with a reason):\n"
        + "\n".join(f"  `{ref}` in {sorted(set(files))}"
                    for ref, files in sorted(unresolved.items()))
    )


def test_allowed_entries_stay_unresolvable():
    """An ALLOWED entry that starts resolving is stale the other way around."""
    walked = walked_files()
    now_real = [ref for ref in ALLOWED if _resolves(ref, walked)]
    assert not now_real, (
        f"ALLOWED entries that now name real files -- remove them: {sorted(now_real)}"
    )
