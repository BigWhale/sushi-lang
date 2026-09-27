"""nori login - authenticate with an Omakase repository."""
import argparse
import getpass
import sys

from sushi_lang.packager.api_client import api_request, ApiError
from sushi_lang.packager.credentials import save_token
from sushi_lang.packager.errors import NoriError
from sushi_lang.packager.repository import resolve_repository


TOKEN_PREFIX = "nori_"


def _read_api_key() -> str:
    """Read the API key without it touching argv."""
    if sys.stdin.isatty():
        return getpass.getpass("API key: ").strip()
    return sys.stdin.readline().strip()


def cmd_login(args: argparse.Namespace) -> int:
    api_key = _read_api_key()
    repository = resolve_repository(args)

    if not api_key.startswith(TOKEN_PREFIX):
        raise NoriError("NE5005", prefix=TOKEN_PREFIX)

    # Verify token against the server
    try:
        user = api_request(repository, "/users/me", token=api_key)
    except ApiError as e:
        if e.status == 401:
            raise NoriError("NE5004", repository=repository) from e
        raise

    username = user.get("username", "unknown")
    save_token(repository, api_key)
    print(f"Logged in as {username} on {repository}")
    return 0
