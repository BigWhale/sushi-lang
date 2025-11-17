from __future__ import annotations
import sys, platform, datetime
import tomllib
from pathlib import Path
from importlib.metadata import version as _pkg_version, PackageNotFoundError

def _read_version_from_pyproject() -> str:
    """
    Read version from pyproject.toml as the single source of truth.

    Returns:
        Version string from pyproject.toml, or "unknown" if unable to read.
    """
    try:
        project_root = Path(__file__).parent.parent
        pyproject_path = project_root / "pyproject.toml"

        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                return data.get("project", {}).get("version", "unknown")
    except Exception:
        pass
    return "unknown"

def _ensure_utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # py3.7+
    except Exception:
        pass

def _get_versions() -> dict[str, str]:
    # Project/package version with fallback chain:
    # 1. Try installed package metadata (when installed via pip/uv)
    # 2. Fall back to pyproject.toml (development mode)
    app_ver = "unknown"

    try:
        app_ver = _pkg_version("sushi-lang")
    except PackageNotFoundError:
        # Not installed as package, read from pyproject.toml
        app_ver = _read_version_from_pyproject()

    # llvmlite + LLVM (best-effort; don’t crash if missing)
    llvmlite_ver = "unknown"
    llvm_lib_ver = "unknown"
    try:
        from llvmlite import binding as llvm
        import llvmlite
        llvmlite_ver = getattr(llvmlite, "__version__", "unknown")
        llvm_lib_ver = ".".join(map(str, (getattr(llvm, "llvm_version_info", None) or ()))) or "unknown"
    except Exception:
        pass

    return {
        "app": app_ver,
        "python": platform.python_version(),
        "llvmlite": llvmlite_ver,
        "llvm": llvm_lib_ver,
    }

def print_banner() -> None:
    _ensure_utf8_stdout()
    v = _get_versions()
    today = datetime.date.today().isoformat()

    # Only use ANSI styling if stdout is a TTY (interactive terminal)
    # This prevents ANSI codes from appearing in piped/redirected output
    use_ansi = sys.stdout.isatty()

    if use_ansi:
        # Minimal ANSI styling for interactive terminals
        BOLD, DIM, RESET = "\x1b[1m", "\x1b[2m", "\x1b[0m"
    else:
        # Plain text for piped/redirected output
        BOLD, DIM, RESET = "", "", ""

    print(
        f"{BOLD} 🍣 Sushi (すし) Lang Compiler{RESET} • {v['app']}\n"
        f"{DIM}Python {v['python']} • llvmlite {v['llvmlite']} • LLVM {v['llvm']} • {today}{RESET}\n"
    )
