"""Shared repository resolution for nori commands."""
import argparse
import os

from sushi_lang.packager.constants import DEFAULT_REPOSITORY, REPOSITORY_ENV_VAR


def resolve_repository(args: argparse.Namespace) -> str:
    """Resolve repository URL: CLI arg > env var > default."""
    if getattr(args, "repository", None):
        return args.repository
    return os.environ.get(REPOSITORY_ENV_VAR, DEFAULT_REPOSITORY)
