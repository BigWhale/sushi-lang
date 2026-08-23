"""The pre-push hook must mirror CI, and that means the environment too.

Git exports its location variables -- GIT_DIR above all -- to every hook it runs,
and a push from a worktree sets GIT_DIR. Every check the hook starts inherits it,
so a check that asks git about a path gets an answer about the repository being
pushed instead. CI runs the same checks with no such variables set, so the hook
that is supposed to predict CI predicted something else and blocked every push
(#442). These cases pin the scrub, and pin the one bypass line in the message.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOK = PROJECT_ROOT / ".githooks" / "pre-push"

# GIT_DIR is the one that breaks a -C. The rest travel with it and re-aim git the
# same way, so they go together.
SCRUBBED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX")


@pytest.fixture(scope="module")
def hook_text() -> str:
    return HOOK.read_text(encoding="utf-8")


def test_the_hook_exists_and_is_executable():
    """A hook that is not executable is a hook git skips without a word."""
    assert HOOK.is_file()
    assert HOOK.stat().st_mode & 0o111


def test_the_hook_scrubs_gits_location_variables(hook_text):
    """Each variable git exports to a hook is removed before a check runs."""
    for name in SCRUBBED:
        assert f"-u {name}" in hook_text, f"{name} survives into the checks"


def test_the_scrub_reaches_every_check(hook_text):
    """The scrub belongs to the runner, not to one favoured check."""
    scrub_lines = [line for line in hook_text.splitlines() if "-u GIT_DIR" in line]
    assert len(scrub_lines) == 1, "one scrub, in one place -- not one per check"

    runs = [line for line in hook_text.splitlines() if re.match(r"\s*run\s", line)]
    assert len(runs) >= 3, "the hook still runs ruff, mypy and pytest"
    for line in runs:
        assert "-u GIT_DIR" not in line, "a per-check scrub drifts; scrub in run()"


def test_every_check_is_launched_through_the_runner(hook_text):
    """A check that bypasses run() bypasses the scrub and the failure count."""
    for label in ("ruff", "mypy", "pytest"):
        assert re.search(rf'^\s*run\s+"{label}"', hook_text, re.MULTILINE), (
            f"{label} must be started by run(), which is what scrubs and counts"
        )


def test_the_hook_is_valid_shell(hook_text):
    """A syntax error in a hook is a push that fails for the wrong reason."""
    for line in hook_text.splitlines():
        if line.strip().startswith("#") or not line.strip():
            continue
        shlex.split(line, comments=True)  # raises ValueError on an unbalanced quote
