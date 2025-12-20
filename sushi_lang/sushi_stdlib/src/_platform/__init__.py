"""
Internal platform-specific implementations for stdlib.

This package contains platform-specific declarations and implementations
that are used internally by stdlib modules. User code should not import
from this package directly.

Structure:
    _platform/
    ├── darwin/    # macOS platform-specific code
    ├── linux/     # Linux platform-specific code (future)
    └── windows/   # Windows platform-specific code (future)
"""
from __future__ import annotations
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from sushi_lang.backend.platform_detect import get_current_platform


def get_platform_module(module_name: str):
    """
    Dynamically import the correct platform-specific module.

    Args:
        module_name: Name of the module (e.g., 'time')

    Returns:
        The platform-specific module

    Example:
        platform_time = get_platform_module('time')
        declare_nanosleep = platform_time.declare_nanosleep
    """
    platform = get_current_platform()

    if platform.is_darwin:
        platform_name = 'darwin'
    elif platform.is_linux:
        platform_name = 'linux'
    elif platform.is_windows:
        platform_name = 'windows'
    else:
        raise RuntimeError(f"Unsupported platform: {platform.os}")

    # Dynamic import: from sushi_lang.sushi_stdlib.src._platform.{platform_name}.{module_name}
    import importlib
    module_path = f"sushi_stdlib.src._platform.{platform_name}.{module_name}"

    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError:
        raise NotImplementedError(
            f"Platform module '{module_name}' not implemented for {platform_name}. "
            f"Expected: sushi_stdlib/src/_platform/{platform_name}/{module_name}.py"
        )
