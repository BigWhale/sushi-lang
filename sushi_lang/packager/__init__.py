"""Nori - Sushi Lang Package Manager."""
import sys

from sushi_lang.packager.cli import cli_main


def main():
    sys.exit(cli_main())
