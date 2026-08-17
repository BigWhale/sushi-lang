"""The auto-rebuild path must be silent (PR4)."""
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
