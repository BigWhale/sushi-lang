"""nori login - authenticate with an Omakase repository."""
import argparse

from sushi_lang.packager.api_client import api_request, ApiError
from sushi_lang.packager.credentials import save_token
from sushi_lang.packager.repository import resolve_repository


TOKEN_PREFIX = "nori_"


def cmd_login(args: argparse.Namespace) -> int:
    api_key = args.api_key
    repository = resolve_repository(args)

    if not api_key.startswith(TOKEN_PREFIX):
        print(f"Invalid API key format. Keys must start with '{TOKEN_PREFIX}'.")
        return 1

    # Verify token against the server
    try:
        user = api_request(repository, "/users/me", token=api_key)
    except ApiError as e:
        if e.status == 401:
            print("Invalid or expired API key.")
            return 1
        print(f"Server error: {e.message}")
        return 1
    except ConnectionError as e:
        print(str(e))
        return 1

    username = user.get("username", "unknown")
    save_token(repository, api_key)
    print(f"Logged in as {username} on {repository}")
    return 0
