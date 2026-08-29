#!/usr/bin/env python3
"""Is this change set a version bump and nothing else?

`pyproject.toml` and `uv.lock` sit inside the workflow's code filter, and they have to:
a dependency bump, a ruff rule, a `requires-python` and the hatch packaging table all
decide whether the compiler still builds and still passes. A release PR touches exactly
those two files and changes exactly one thing in each -- the version -- and that decides
nothing. Without this script a release waits for the whole cross-platform suite to
re-prove the commit it was cut from.

The comparison is semantic, not textual. Both sides are parsed, the version is set to
one value on both, and what is left must be equal. A reordered table, a re-wrapped list
or a new comment therefore rides along, and any real edit does not.

Every failure answers "no". A file that will not parse, a base blob git cannot produce,
a manifest with no version: none of them are a release, and none of them may skip a
suite. The script is only ever allowed to turn tests OFF, so its errors must turn them
back on.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib

PYPROJECT = "pyproject.toml"
LOCK = "uv.lock"

# The files a release PR may touch. The workflow's path filter has already decided that
# nothing under `sushi_lang/`, `tests/`, `.github/` or `sushic` changed; this list is
# what remains that this script is willing to vouch for.
RELEASE_PATHS = frozenset({PYPROJECT, LOCK, "CHANGELOG.md", "README.md"})

PROJECT_NAME = "sushi-lang"


def _parse(text: str | None) -> dict | None:
    """Parsed TOML, or None when there is nothing readable to compare."""
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def pyproject_is_version_only(base: str | None, head: str | None) -> bool:
    """True when `project.version` is the only thing that differs."""
    base_doc, head_doc = _parse(base), _parse(head)
    if base_doc is None or head_doc is None:
        return False

    head_version = head_doc.get("project", {}).get("version")
    base_version = base_doc.get("project", {}).get("version")
    if not head_version or not base_version:
        return False

    base_doc["project"]["version"] = head_version
    return base_doc == head_doc


def lock_is_version_only(base: str | None, head: str | None) -> bool:
    """True when the project's own entry is the only thing that differs.

    The lock records every resolved dependency, so a dependency change shows up here
    even when `pyproject.toml` states a range that did not move.
    """
    base_doc, head_doc = _parse(base), _parse(head)
    if base_doc is None or head_doc is None:
        return False

    base_entry = _project_entry(base_doc)
    head_entry = _project_entry(head_doc)
    if base_entry is None or head_entry is None:
        return False

    base_entry["version"] = head_entry.get("version")
    return base_doc == head_doc


def _project_entry(doc: dict) -> dict | None:
    for package in doc.get("package", []):
        if package.get("name") == PROJECT_NAME:
            return package
    return None


CHECKS = {PYPROJECT: pyproject_is_version_only, LOCK: lock_is_version_only}


def decide(changed: list[str], blobs: dict[str, tuple[str | None, str | None]]) -> bool:
    """True when every changed file is a release file and every version check passes.

    `blobs` maps a path to its (base, head) content. A path with no entry is treated as
    unreadable, which answers no.
    """
    if not changed:
        return False
    if not set(changed) <= RELEASE_PATHS:
        return False

    for path, check in CHECKS.items():
        if path in changed:
            base, head = blobs.get(path, (None, None))
            if not check(base, head):
                return False
    return True


def _git(*args: str) -> str | None:
    """stdout of a git command, or None when git cannot answer."""
    try:
        done = subprocess.run(("git", *args), capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="the ref the pull request merges into")
    parser.add_argument("head", help="the ref the pull request proposes")
    args = parser.parse_args(argv)

    names = _git("diff", "--name-only", f"{args.base}..{args.head}")
    if names is None:
        print("false")
        return 0

    changed = [line for line in names.splitlines() if line]
    blobs = {
        path: (_git("show", f"{args.base}:{path}"), _git("show", f"{args.head}:{path}"))
        for path in CHECKS
        if path in changed
    }
    print("true" if decide(changed, blobs) else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
