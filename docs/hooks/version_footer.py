"""MkDocs hook: put the version and the build date in the site footer.

The site is built from main and carries no version of its own, so a reader cannot
tell which compiler the pages describe. This hook writes the release version, the
build date and the source commit into `config.copyright`, which Material renders in
the footer of every page.

The version comes from pyproject.toml, not from the installed package: the docs
build runs in a throw-away mkdocs environment that does not have sushi_lang. A
version that cannot be read stops the build. An "unknown" in the footer is the
same defect as no footer at all.

The repository does not build versioned documentation. The site always shows the
current state of main, and this stamp says which state that is.
"""
from __future__ import annotations

import datetime
import os
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMIT_URL = "https://github.com/bigwhale/sushi-lang/commit"


class FooterError(Exception):
    """The footer cannot be stamped. The build must stop."""


def read_version(repo_root: Path) -> str:
    """Read the release version from pyproject.toml."""
    pyproject = repo_root / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FooterError(f"cannot read the version from {pyproject}: {exc}") from exc

    version = data.get("project", {}).get("version")
    if not version:
        raise FooterError(f"{pyproject} has no project.version")
    return str(version)


def _environment_without_git() -> dict[str, str]:
    """The current environment with every GIT_* variable removed.

    Git exports its own variables to any process it starts -- a hook gets GIT_DIR,
    and a push from a worktree always sets it -- and GIT_DIR overrides an explicit
    -C. Inherited, they turn the question below from "what is HEAD in this
    directory" into "what is HEAD in whichever repository invoked us". The
    argument is the question, so nothing in the environment may re-aim it.
    """
    return {name: value for name, value in os.environ.items()
            if not name.startswith("GIT_")}


def read_commit(repo_root: Path) -> str | None:
    """Read the short commit of HEAD in `repo_root`. None when git cannot answer.

    `safe.directory` is set because a CI job that runs in a container checks the
    repository out as another user, and git then refuses to read it. The
    repository is already trusted here: this file runs from it.
    """
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(repo_root),
             "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
            env=_environment_without_git(),
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def build_date() -> str:
    """Today, as an ISO day in UTC."""
    return datetime.datetime.now(datetime.UTC).date().isoformat()


def footer_text(version: str, date: str, commit: str | None) -> str:
    parts = [f"Sushi Lang {version}", f"documentation generated {date}"]
    if commit:
        parts.append(f'commit <a href="{COMMIT_URL}/{commit}">{commit}</a>')
    return " &middot; ".join(parts)


def on_config(config):
    config["copyright"] = footer_text(
        read_version(REPO_ROOT), build_date(), read_commit(REPO_ROOT)
    )
    return config
