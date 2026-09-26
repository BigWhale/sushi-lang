"""No name under `sushi_lang/` that nothing reads (#918).

Ruff sees one module at a time, so a function no module calls, an attribute no
reader reads, or a module nothing imports passes it. vulture sees the whole package.
This gate runs vulture at `--min-confidence 60` over `sushi_lang` and refuses every
candidate that `vulture_whitelist.WHITELIST` does not explain and that
`vulture_whitelist.RATCHET` does not record. A whitelist or ratchet entry that
matches no candidate fails too, so neither list keeps a name that is gone.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from vulture_whitelist import IGNORE_DECORATORS, RATCHET, WHITELIST

ROOT = Path(__file__).resolve().parents[2]
_LINE = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+): (?P<message>.*?'(?P<name>[^']+)'.*)$")


def vulture_candidates() -> list[tuple[str, str, str]]:
    """Every vulture candidate as `(path, name, the whole line)`."""
    if importlib.util.find_spec("vulture") is None:
        pytest.fail("vulture is not installed. It is in the `dev` extras: "
                    "run `uv sync --extra dev`, then `uv run pytest`.")
    proc = subprocess.run(
        [sys.executable, "-m", "vulture", "sushi_lang", "--min-confidence", "60",
         "--ignore-decorators", ",".join(IGNORE_DECORATORS)],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    # vulture exits 3 when it finds dead code and 0 when it finds none.
    if proc.returncode not in (0, 3):
        pytest.fail(f"vulture failed (exit {proc.returncode}):\n{proc.stderr}")
    found = []
    for line in proc.stdout.splitlines():
        match = _LINE.match(line)
        if match is None:
            pytest.fail(f"cannot read this vulture line: {line!r}")
        found.append((match["path"], match["name"], line))
    return found


def _whitelisted(path: str, name: str) -> bool:
    return any(fnmatchcase(path, p) and fnmatchcase(name, n) for p, n, _ in WHITELIST)


@pytest.fixture(scope="module")
def candidates():
    return vulture_candidates()


def test_every_candidate_is_explained(candidates):
    ratchet = {(p, n) for p, n, _ in RATCHET}
    new = [line for path, name, line in candidates
           if not _whitelisted(path, name) and (path, name) not in ratchet]
    assert not new, (
        "vulture found code that nothing reads. Delete it. If a path vulture cannot "
        "see reads it (getattr with a built name, a protocol of another library, a "
        "gate test), add ONE entry with its reason to tests/unit/vulture_whitelist.py:\n  "
        + "\n  ".join(new))


def test_every_whitelist_entry_matches_a_candidate(candidates):
    stale = [(p, n) for p, n, _ in WHITELIST
             if not any(fnmatchcase(path, p) and fnmatchcase(name, n)
                        for path, name, _line in candidates)]
    assert not stale, f"these WHITELIST entries match nothing; remove them: {stale}"


def test_the_ratchet_only_gets_shorter(candidates):
    found = {(path, name) for path, name, _line in candidates}
    gone = [(p, n) for p, n, _ in RATCHET if (p, n) not in found]
    assert not gone, f"these RATCHET entries are resolved; remove them: {gone}"


def test_every_entry_carries_a_reason():
    for entry in (*WHITELIST, *RATCHET):
        assert entry[2].strip(), entry
    for pattern, reason in IGNORE_DECORATORS.items():
        assert reason.strip(), pattern
