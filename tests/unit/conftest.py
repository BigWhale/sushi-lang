"""Shared hooks for the unit-test layer. No test here runs the Sushi compiler; the test
runner's own tests are the one exception."""
from __future__ import annotations


def pytest_collection_modifyitems(items):
    """Mark every test in a subprocess-spawning module `slow`.

    Derived rather than hand-applied: the modules that qualify, and a marker maintained by
    hand across that many files is one a new E2E module forgets. Importing `subprocess` is
    what makes a module slow here -- it means sushic, clang or the packager CLI is spawned.
    """
    for item in items:
        module = getattr(item, "module", None)
        if module is not None and hasattr(module, "subprocess"):
            item.add_marker("slow")
