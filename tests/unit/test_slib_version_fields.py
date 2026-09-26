"""A `.slib` states its own version, and which compilers may consume it.

`compiler_version` has always been recorded and never enforced. Version 4 adds
`library_version` (what this library is) and `requires_compiler` (which compilers accept
it), and the load path enforces the second. See `docs/design/libraries.md` section 6.
"""
from __future__ import annotations


import pytest

from sushi_lang.backend.library_errors import LibraryError







# --- The manifest carries the new fields ------------------------------------















# --- Where the library version comes from -----------------------------------











# --- Enforcement of requires_compiler ---------------------------------------

def _check(requires, current="0.11.1", ignore=False):
    from sushi_lang.compiler.pipeline import _check_library_compiler_version

    _check_library_compiler_version(
        {"library_name": "versionlib", "requires_compiler": requires},
        "lib/versionlib", current=current, ignore=ignore)


@pytest.mark.parametrize("requires,current", [
    ("~0.11", "0.11.1"),
    ("~0.11", "0.11.0"),
    ("~0.11", "0.11.99"),
    (">=0.10.0, <1.0.0", "0.11.1"),
    ("0.11.1", "0.11.1"),
])
def test_a_satisfied_requirement_loads(requires, current):
    _check(requires, current)


@pytest.mark.parametrize("requires,current", [
    ("~0.11", "0.12.0"),
    ("~0.11", "0.10.9"),
    ("~0.12", "0.11.1"),
    ("0.11.1", "0.11.2"),
])
def test_an_unsatisfied_requirement_is_rejected(requires, current):
    with pytest.raises(LibraryError) as excinfo:
        _check(requires, current)
    assert excinfo.value.code == "CE3503"


def test_the_override_flag_lifts_the_rejection():
    _check("~0.11", current="0.12.0", ignore=True)


@pytest.mark.parametrize("metadata", [
    {},                                     # a field that is simply absent
    {"requires_compiler": None},
    {"requires_compiler": ""},
    {"requires_compiler": "not-a-constraint"},
])
def test_an_unreadable_requirement_does_not_block(metadata):
    # The gate exists to catch a real incompatibility, never to fail a build over a
    # field it could not parse.
    from sushi_lang.compiler.pipeline import _check_library_compiler_version

    _check_library_compiler_version(metadata, "lib/x", current="0.11.1")


def test_an_unknown_running_compiler_does_not_block():
    # `sushi_lang.__version__` falls back to "unknown" when the package metadata and
    # pyproject.toml are both unreadable. That must not stop a build.
    _check("~0.11", current="unknown")


# --- End to end --------------------------------------------------------------

