"""Internal platform-specific implementations for stdlib."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from sushi_lang.backend.platform_detect import get_current_platform


def get_platform_module(module_name: str):
    """Dynamically import the correct platform-specific module."""
    platform = get_current_platform()

    if platform.is_darwin:
        platform_name = 'darwin'
    elif platform.is_linux:
        platform_name = 'linux'
    elif platform.is_windows:
        platform_name = 'windows'
    else:
        raise RuntimeError(f"Unsupported platform: {platform.os}")

    import importlib
    module_path = f"sushi_stdlib.src._platform.{platform_name}.{module_name}"

    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError:
        raise NotImplementedError(
            f"Platform module '{module_name}' not implemented for {platform_name}. "
            f"Expected: sushi_stdlib/src/_platform/{platform_name}/{module_name}.py"
        ) from None
