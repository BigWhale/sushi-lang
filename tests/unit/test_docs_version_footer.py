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


def test_commit_is_the_short_head_in_a_checkout():
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert vf.read_commit(PROJECT_ROOT) == head


def test_on_config_stamps_the_copyright():
    """Material renders `copyright` in the footer. The hook writes it there."""
    config = {"copyright": None}
    vf.on_config(config)
    assert config["copyright"]
    assert vf.read_version(PROJECT_ROOT) in config["copyright"]
