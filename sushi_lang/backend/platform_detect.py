"""Platform detection and target triple parsing for Sushi compiler."""
from __future__ import annotations
from dataclasses import dataclass
from llvmlite import binding as llvm


@dataclass
class TargetPlatform:
    """Represents a compilation target platform."""
    arch: str      # arm64, x86_64, riscv64, etc.
    vendor: str    # apple, pc, unknown, etc.
    os: str        # darwin, linux, windows, etc.
    abi: str       # (empty), gnu, musl, etc.

    @property
    def is_unix(self) -> bool:
        """Returns True if target is Unix-like (macOS, Linux, BSD)."""
        return self.os in {'darwin', 'linux', 'freebsd', 'openbsd', 'netbsd'}

    @property
    def is_darwin(self) -> bool:
        """Returns True if target is macOS."""
        return self.os == 'darwin'

    @property
    def is_linux(self) -> bool:
        """Returns True if target is Linux."""
        return self.os == 'linux'

    @property
    def is_windows(self) -> bool:
        """Returns True if target is Windows."""
        return self.os == 'windows'

    @property
    def triple(self) -> str:
        """Reconstruct the target triple string."""
        parts = [self.arch, self.vendor, self.os]
        if self.abi:
            parts.append(self.abi)
        return '-'.join(parts)


def parse_triple(triple: str) -> TargetPlatform:
    """Parse an LLVM target triple into components."""
    parts = triple.split('-')

    os_part = parts[2] if len(parts) > 2 else 'unknown'
    if '.' in os_part:
        os_part = os_part.split('.')[0]  # darwin25.0.0 -> darwin25
        if os_part.startswith('darwin'):
            os_part = 'darwin'

    return TargetPlatform(
        arch=parts[0] if len(parts) > 0 else 'unknown',
        vendor=parts[1] if len(parts) > 1 else 'unknown',
        os=os_part,
        abi=parts[3] if len(parts) > 3 else '',
    )


def get_current_platform() -> TargetPlatform:
    """Get the platform for the current compilation."""
    return parse_triple(llvm.get_default_triple())


def current_platform_name() -> str:
    """The host platform as the short name a `.slib` records in its metadata."""
    platform = get_current_platform()
    return "darwin" if platform.is_darwin else "linux" if platform.is_linux else "unknown"


def default_triple() -> str:
    """The host target triple, verbatim. The one reader outside the backend is the
    incremental cache, which folds it into its key."""
    return llvm.get_default_triple()
