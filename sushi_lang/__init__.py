"""Sushi Lang - A compiled language with LLVM backend."""
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("sushi-lang")
    __dev__ = False
except PackageNotFoundError:
    # Development mode - read from pyproject.toml
    import tomllib
    from pathlib import Path
    try:
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as f:
            __version__ = tomllib.load(f).get("project", {}).get("version", "unknown")
    except Exception:
        __version__ = "unknown"
    __dev__ = True
