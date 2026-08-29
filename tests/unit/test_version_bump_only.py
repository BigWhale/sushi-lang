"""A release PR must not run the full suite, and nothing else may claim that exemption.

`pyproject.toml` and `uv.lock` are inside the CI code filter, because a dependency
bump, a ruff rule or the hatch packaging table all decide whether the compiler still
works. A version bump decides nothing, and it is the only change a release PR makes to
those two files. The script under test is the one place that tells the two apart.

The rule is semantic, not textual: both sides are parsed, the version is set aside, and
what is left must be equal. Each case here is one way a change could smuggle itself past
that comparison, or one way an honest release could be refused it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / ".github" / "scripts" / "version_bump_only.py"


def _load_module():
    """`.github/scripts` is not a package, so import the file by path."""
    spec = importlib.util.spec_from_file_location("version_bump_only", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vbo = _load_module()


def _pyproject(version: str, *, extra_dep: str = "", ruff_line: str = "line-length = 100") -> str:
    deps = '  "colorama",\n'
    if extra_dep:
        deps += f'  "{extra_dep}",\n'
    return (
        "[project]\n"
        'name = "sushi-lang"\n'
        f'version = "{version}"\n'
        "dependencies = [\n"
        f"{deps}"
        "]\n"
        "\n"
        "[tool.ruff]\n"
        f"{ruff_line}\n"
    )


def _lock(version: str, *, other: str = "0.4.6") -> str:
    return (
        "version = 1\n"
        "\n"
        "[[package]]\n"
        'name = "colorama"\n'
        f'version = "{other}"\n'
        "\n"
        "[[package]]\n"
        'name = "sushi-lang"\n'
        f'version = "{version}"\n'
        'source = {{ editable = "." }}\n'.replace("{{", "{").replace("}}", "}")
    )


# --- pyproject.toml ----------------------------------------------------------------

def test_a_bare_version_bump_is_version_only():
    assert vbo.pyproject_is_version_only(_pyproject("0.11.1"), _pyproject("0.12.0"))


def test_an_unchanged_file_is_version_only():
    """A release PR may touch the lock and not the manifest, or the reverse."""
    assert vbo.pyproject_is_version_only(_pyproject("0.12.0"), _pyproject("0.12.0"))


def test_a_new_dependency_is_not_version_only():
    base = _pyproject("0.11.1")
    head = _pyproject("0.12.0", extra_dep="requests")
    assert not vbo.pyproject_is_version_only(base, head)


def test_a_changed_tool_setting_is_not_version_only():
    base = _pyproject("0.11.1")
    head = _pyproject("0.12.0", ruff_line="line-length = 120")
    assert not vbo.pyproject_is_version_only(base, head)


def test_a_comment_only_change_is_version_only():
    """A comment cannot change what the build does, so it may ride along."""
    base = _pyproject("0.11.1")
    head = "# a note for the next reader\n" + _pyproject("0.12.0")
    assert vbo.pyproject_is_version_only(base, head)


def test_a_missing_version_is_not_version_only():
    head = _pyproject("0.12.0").replace('version = "0.12.0"\n', "")
    assert not vbo.pyproject_is_version_only(_pyproject("0.11.1"), head)


def test_unparseable_toml_is_not_version_only():
    """A file the parser cannot read is not a file this script may clear."""
    assert not vbo.pyproject_is_version_only(_pyproject("0.11.1"), "[project\nname =")


# --- uv.lock -----------------------------------------------------------------------

def test_a_lock_version_bump_is_version_only():
    assert vbo.lock_is_version_only(_lock("0.11.1"), _lock("0.12.0"))


def test_a_bumped_dependency_in_the_lock_is_not_version_only():
    base = _lock("0.11.1")
    head = _lock("0.12.0", other="0.4.7")
    assert not vbo.lock_is_version_only(base, head)


def test_a_lock_without_the_project_entry_is_not_version_only():
    head = _lock("0.12.0").replace('name = "sushi-lang"', 'name = "something-else"')
    assert not vbo.lock_is_version_only(_lock("0.11.1"), head)


# --- the decision the workflow reads ------------------------------------------------

@pytest.mark.parametrize("changed", [
    ["pyproject.toml"],
    ["uv.lock"],
    ["pyproject.toml", "uv.lock"],
    ["pyproject.toml", "uv.lock", "CHANGELOG.md", "README.md"],
])
def test_a_release_shaped_change_set_is_version_only(changed):
    assert vbo.decide(
        changed,
        {"pyproject.toml": (_pyproject("0.11.1"), _pyproject("0.12.0")),
         "uv.lock": (_lock("0.11.1"), _lock("0.12.0"))},
    )


def test_a_source_file_alongside_the_bump_is_not_version_only():
    """The path filter should already have caught this; the script must not disagree."""
    assert not vbo.decide(
        ["pyproject.toml", "sushi_lang/compiler/cli.py"],
        {"pyproject.toml": (_pyproject("0.11.1"), _pyproject("0.12.0"))},
    )


def test_an_unreadable_side_is_refused():
    """A base blob git cannot hand over must fail closed, never open."""
    assert not vbo.decide(["pyproject.toml"], {"pyproject.toml": (None, _pyproject("0.12.0"))})
