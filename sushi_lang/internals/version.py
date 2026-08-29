from __future__ import annotations
import io
import sys
import platform
import datetime

from sushi_lang import __version__ as app_ver, __dev__ as is_dev

def _ensure_utf8_stdout() -> None:
    try:
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def _get_versions() -> dict[str, str]:

    # The backend owns the llvmlite import (IR.md Phase 0); it answers best-effort.
    from sushi_lang.backend.toolchain import llvm_versions
    llvmlite_ver, llvm_lib_ver = llvm_versions()

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

    # THE colour decision, the same one a diagnostic makes. Deciding on `isatty` alone
    # here meant `NO_COLOR` silenced every diagnostic and left this line painted.
    from sushi_lang.internals.styling import Palette, should_colour

    p = Palette(should_colour(sys.stdout))
    BOLD, DIM, RESET = p.bold, p.dim, p.reset

    dev_marker = " (dev)" if is_dev else ""
    print(
        f"{BOLD} 🍣 Sushi (すし) Lang Compiler{RESET} • {v['app']}{dev_marker}\n"
        f"{DIM}Python {v['python']} • llvmlite {v['llvmlite']} • LLVM {v['llvm']} • {today}{RESET}\n"
    )
