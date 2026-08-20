"""Every path-shaped reference in tracked documentation and comments names a real file.

The gate #366 asked for: a backtick-quoted `dir/file.py` reference that resolves to no
tracked file is a fact that has already drifted -- the seven references that issue lists
each named `semantics/passes/borrow.py` after #362 deleted it, and one of them was stale
the day it was written. A reference resolves if a tracked path equals it or ends with it
(so `borrow/calls.py` matches `sushi_lang/semantics/passes/borrow/calls.py`); an optional
`:line` or `:symbol` suffix is ignored. Bare filenames with no directory component are
not asserted -- `borrow.py` alone is ambiguous by design (issue #366).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PATH_REF = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*/[A-Za-z0-9_.-]+\.(?:py|sushi|lark|md))(?::[^`]*)?`"
)

# Files whose references are frozen history: a changelog entry describes the tree at the
# release it records, so a path deleted since is correct there. This module exempts
# itself for the same reason a linter does: its docstring must be able to NAME a broken
# reference as the example.
FROZEN = {"CHANGELOG.md", "tests/unit/test_path_references_exist.py"}

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
}


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                         cwd=PROJECT_ROOT)
    assert out.returncode == 0, f"git ls-files failed: {out.stderr.strip()}"
    return out.stdout.splitlines()


def test_path_references_resolve():
    tracked = tracked_files()
    tracked_set = set(tracked)

    def resolves(ref: str) -> bool:
        return ref in tracked_set or any(p.endswith("/" + ref) for p in tracked)

    unresolved: dict[str, list[str]] = {}
    for f in tracked:
        if not f.endswith((".md", ".py", ".sushi")) or f in FROZEN:
            continue
        text = (PROJECT_ROOT / f).read_text(errors="replace")
        for m in PATH_REF.finditer(text):
            ref = m.group(1)
            if ref in ALLOWED or resolves(ref):
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
    tracked = tracked_files()
    tracked_set = set(tracked)
    now_real = [ref for ref in ALLOWED
                if ref in tracked_set or any(p.endswith("/" + ref) for p in tracked)]
    assert not now_real, (
        f"ALLOWED entries that now name real files -- remove them: {sorted(now_real)}"
    )
