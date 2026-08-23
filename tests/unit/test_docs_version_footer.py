"""The documentation footer must name the version it describes.

The site is built from main and had no version stamp, so a reader could not tell
which compiler the pages document. The hook below writes the version, the build
date and the source commit into the footer. Each case here is one way a stamp can
go wrong: a stale hard-coded version, a silent "unknown", or a missing date.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOK = PROJECT_ROOT / "docs" / "hooks" / "version_footer.py"


def _load_module():
    """`docs/hooks` is not a package, so import the file by path."""
    spec = importlib.util.spec_from_file_location("version_footer", HOOK)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vf = _load_module()


def _pyproject(tmp_path: Path, version: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "sushi-lang"\nversion = "{version}"\n', encoding="utf-8"
    )
    return root


def test_version_comes_from_pyproject(tmp_path):
    """The stamp reads the release version, so a bump moves the footer with it."""
    assert vf.read_version(_pyproject(tmp_path, "9.9.9")) == "9.9.9"


def test_unreadable_version_fails_the_build(tmp_path):
    """A missing version must stop the build, not publish an "unknown" footer."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(vf.FooterError):
        vf.read_version(empty)


def test_version_without_a_value_fails_the_build(tmp_path):
    """A pyproject with no version field is the same failure as no pyproject."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "sushi-lang"\n', encoding="utf-8")
    with pytest.raises(vf.FooterError):
        vf.read_version(root)


def test_the_repository_version_is_readable():
    """The real pyproject must satisfy the hook. This is the gate on a rename."""
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", vf.read_version(PROJECT_ROOT))


def test_footer_names_the_version_and_the_date():
    text = vf.footer_text("0.11.1", "2026-08-22", None)
    assert "0.11.1" in text
    assert "2026-08-22" in text


def test_footer_links_the_commit_when_one_is_known():
    text = vf.footer_text("0.11.1", "2026-08-22", "abc1234")
    assert "abc1234" in text
    assert "https://github.com/bigwhale/sushi-lang/commit/abc1234" in text


def test_footer_omits_the_commit_outside_a_checkout():
    """A tarball build has no git data. The stamp drops the commit, it does not fake one."""
    assert "commit" not in vf.footer_text("0.11.1", "2026-08-22", None)


def test_build_date_is_an_iso_day():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", vf.build_date())


def test_commit_is_none_outside_a_checkout(tmp_path):
    assert vf.read_commit(tmp_path) is None


def test_commit_is_git_answer_or_nothing():
    """The stamp is git's answer, or nothing at all. It never invents a commit.

    A CI job that runs in a container checks the repository out as another user, and
    git refuses to read it. The hook must report nothing there, not crash and not guess.
    """
    probe = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(PROJECT_ROOT),
         "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    commit = vf.read_commit(PROJECT_ROOT)
    if probe.returncode == 0:
        assert commit == probe.stdout.strip()
    else:
        assert commit is None


def test_on_config_stamps_the_copyright():
    """Material renders `copyright` in the footer. The hook writes it there."""
    config = {"copyright": None}
    vf.on_config(config)
    assert config["copyright"]
    assert vf.read_version(PROJECT_ROOT) in config["copyright"]


# Git exports its own location variables to every hook it runs, and GIT_DIR overrides
# an explicit -C. A push from a worktree therefore hands the whole pytest run a pointer
# to the repository being pushed, which is how #442 turned this file red inside the
# pre-push hook while CI, which runs pytest as an ordinary step, stayed green.
def _hook_environment(root: Path) -> dict[str, str]:
    """What git sets for a hook, aimed at `root`."""
    return {
        "GIT_DIR": str(root / ".git"),
        "GIT_WORK_TREE": str(root),
        "GIT_INDEX_FILE": str(root / ".git" / "index"),
        "GIT_PREFIX": "",
    }


def _real_head() -> str | None:
    probe = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(PROJECT_ROOT),
         "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    return probe.stdout.strip() if probe.returncode == 0 else None


def test_commit_ignores_an_inherited_git_dir(tmp_path, monkeypatch):
    """An inherited GIT_DIR must not answer for a directory that is not a checkout."""
    monkeypatch.setenv("GIT_DIR", str(PROJECT_ROOT / ".git"))
    assert vf.read_commit(tmp_path) is None


def test_commit_ignores_a_whole_hook_environment(tmp_path, monkeypatch):
    """The same, with every location variable git hands a hook set at once."""
    for name, value in _hook_environment(PROJECT_ROOT).items():
        monkeypatch.setenv(name, value)
    assert vf.read_commit(tmp_path) is None


def test_commit_answers_about_the_repository_it_is_given(tmp_path, monkeypatch):
    """The argument is the question. A GIT_DIR pointing elsewhere cannot re-aim it.

    The expected answer is read before the environment is dirtied, or the probe
    would inherit the same GIT_DIR and the two would agree on being wrong.
    """
    expected = _real_head()
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-a-repository.git"))
    assert vf.read_commit(PROJECT_ROOT) == expected
