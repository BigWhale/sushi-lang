from __future__ import annotations
import sys, platform, datetime

MAJOR = 0
MINOR = 0
PATCH = 11
VERSION = f"{MAJOR}.{MINOR}.{PATCH}"

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
except Exception:  # py<3.8 fallback if you ever need it
    _pkg_version = None
    PackageNotFoundError = Exception

def _ensure_utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # py3.7+
    except Exception:
        pass

def _get_versions() -> dict[str, str]:
    # Project/package version (edit the package name if you publish it)
    app_ver = VERSION
    if _pkg_version is not None:
        try:
            app_ver = _pkg_version("sushi-lang")
        except PackageNotFoundError:
            pass

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
        f"{BOLD} 🍣 Sushi (すし) Lang Compiler{RESET}  v{v['app']}\n"
        f"{DIM}Python {v['python']} • llvmlite {v['llvmlite']} • LLVM {v['llvm']} • {today}{RESET}\n"
    )
