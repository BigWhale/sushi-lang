"""
Common POSIX-compliant platform implementations.

Contains platform-agnostic implementations that work across all POSIX systems
(macOS, Linux, BSD, etc.). Platform-specific variations are handled by
the darwin/ and linux/ modules which may import and extend these base implementations.
"""

__all__ = ['time', 'random', 'env', 'stdio', 'files']
