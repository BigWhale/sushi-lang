"""Internal platform-specific implementations for stdlib."""
from __future__ import annotations

import importlib
from types import ModuleType

from sushi_lang.backend.platform_detect import get_current_platform


def get_platform_module(module_name: str) -> ModuleType:
    """Import the platform-specific module of the build platform."""
    platform = get_current_platform()

    if platform.is_darwin:
        platform_name = 'darwin'
    elif platform.is_linux:
        platform_name = 'linux'
    else:
        raise RuntimeError(f"Unsupported platform: {platform.os}")

    module_path = f"{__name__}.{platform_name}.{module_name}"

    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError:
        raise NotImplementedError(
            f"Platform module '{module_name}' not implemented for {platform_name}. "
            f"Expected: sushi_lang/sushi_stdlib/src/_platform/{platform_name}/{module_name}.py"
        ) from None
