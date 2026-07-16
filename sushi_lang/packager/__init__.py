"""Nori - Sushi Lang Package Manager."""
from sushi_lang.packager.cli import cli_main


def main() -> int:
    """Console-script entry point. Returns the exit code (like compiler.main)
    instead of calling sys.exit itself, so it is callable from tests."""
    return cli_main()
