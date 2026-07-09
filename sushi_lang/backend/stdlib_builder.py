"""On-the-fly stdlib bitcode builder.

The precompiled stdlib `.bc` under `sushi_stdlib/dist/{platform}/` are build
artifacts (not tracked in git). This module keeps the current platform's `.bc`
present and fresh: it compares a content fingerprint of the generator sources
against a per-platform marker and rebuilds via `build.py` when they diverge, so
editing an IR-generator is picked up on the next compile instead of silently
linking stale bitcode.
"""
from __future__ import annotations

from pathlib import Path

# sushi_lang/backend/stdlib_builder.py -> sushi_lang/sushi_stdlib/dist
_DIST_DIR = Path(__file__).resolve().parent.parent / "sushi_stdlib" / "dist"
_MARKER_NAME = ".build_fingerprint"

# Run the freshness check at most once per process (per platform).
_checked: set[str] = set()


def detect_platform() -> str:
    """Return the current platform name ("darwin"/"linux") or raise."""
    from sushi_lang.backend.platform_detect import get_current_platform

    platform = get_current_platform()
    if platform.is_darwin:
        return "darwin"
    elif platform.is_linux:
        return "linux"
    raise RuntimeError(f"Unsupported platform for stdlib build: {platform.os}")


def _marker_path(platform_name: str) -> Path:
    return _DIST_DIR / platform_name / _MARKER_NAME


def write_build_marker(platform_name: str) -> None:
    """Record the generator-source fingerprint for a freshly built platform dir."""
    from sushi_lang.compiler.fingerprint import compute_stdlib_source_fingerprint

    marker = _marker_path(platform_name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(compute_stdlib_source_fingerprint(), encoding="utf-8")


def _is_fresh(platform_name: str) -> bool:
    from sushi_lang.compiler.fingerprint import compute_stdlib_source_fingerprint

    platform_dir = _DIST_DIR / platform_name
    if not platform_dir.is_dir():
        return False
    marker = _marker_path(platform_name)
    if not marker.exists():
        return False
    try:
        stored = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return stored == compute_stdlib_source_fingerprint()


def ensure_stdlib_built(platform_name: str | None = None) -> None:
    """Auto-build the current platform's stdlib `.bc` if missing or stale.

    Memoized per process, so it runs at most once per compile. On a fingerprint
    mismatch (or a missing dist dir/marker) it prints a one-line notice, rebuilds
    via `build_all`, and refreshes the marker.
    """
    if platform_name is None:
        platform_name = detect_platform()

    if platform_name in _checked:
        return

    if _is_fresh(platform_name):
        _checked.add(platform_name)
        return

    print("Rebuilding stdlib (generator source changed or bitcode missing)...")
    from sushi_lang.sushi_stdlib.build import build_all

    build_all(platform_name, quiet=True)
    write_build_marker(platform_name)
    _checked.add(platform_name)
