"""What the backend was built against.

Only `--version` reads this. It lives in the backend because that is the one layer
allowed to name llvmlite (IR.md Phase 0), and a version banner is not a reason to make
an exception.
"""
from __future__ import annotations


def llvm_versions() -> tuple[str, str]:
    """`(llvmlite version, LLVM library version)`.

    Best effort: a missing or broken llvmlite reports "unknown" twice rather than
    stopping a `--version` that the user ran to find out what is broken.
    """
    try:
        import llvmlite
        from llvmlite import binding as llvm

        lite = getattr(llvmlite, "__version__", "unknown")
        info = getattr(llvm, "llvm_version_info", None) or ()
        return lite, ".".join(map(str, info)) or "unknown"
    except Exception:
        return "unknown", "unknown"
