"""A stdlib platform module has one name, and the loader changes no `sys.path` (#904).

The probe runs in a subprocess: a module another test imported first must not hide a
second name or a `sys.path` change that the loader makes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest














def test_an_unsupported_platform_is_one_clear_error(monkeypatch):
    import sushi_lang.sushi_stdlib.src._platform as platform_pkg

    other = SimpleNamespace(is_darwin=False, is_linux=False, is_windows=True, os="windows")
    monkeypatch.setattr(platform_pkg, "get_current_platform", lambda: other)
    with pytest.raises(RuntimeError, match="Unsupported platform: windows"):
        platform_pkg.get_platform_module("files")
