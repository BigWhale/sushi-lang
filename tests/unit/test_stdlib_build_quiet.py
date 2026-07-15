"""The auto-rebuild path must be silent (PR4).

`ensure_stdlib_built()` calls `build_all(platform, quiet=True)` during an ordinary
`sushic foo.sushi` when the stdlib is stale. Before PR4 only 3 of build_all's prints
were gated, so a normal compile emitted ~11 lines of "Building collections/strings..."
banners. This pins that `quiet=True` produces no stdout.
"""
from __future__ import annotations

import contextlib
import io

from sushi_lang.backend.platform_detect import current_platform_name
from sushi_lang.sushi_stdlib.build import build_all


def test_quiet_build_emits_nothing():
    platform = current_platform_name()
    if platform == "unknown":
        import pytest
        pytest.skip("unknown host platform")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        build_all(platform, quiet=True)
    assert buf.getvalue() == "", (
        "the auto-rebuild path must be silent; got:\n" + buf.getvalue()
    )


def test_loud_build_still_reports_progress():
    """quiet=False (the explicit --build-stdlib path) keeps its banners."""
    platform = current_platform_name()
    if platform == "unknown":
        import pytest
        pytest.skip("unknown host platform")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        build_all(platform, quiet=False)
    out = buf.getvalue()
    assert "Building" in out and "→" in out
