"""nori status - show login status and published packages."""
import argparse
import sys

from sushi_lang.packager.api_client import api_request, ApiError
from sushi_lang.packager.credentials import load_token
from sushi_lang.packager.repository import resolve_repository


def cmd_status(args: argparse.Namespace) -> int:
    repository = resolve_repository(args)
    token = load_token(repository)

    if not token:
        print(f"Not logged in to {repository}.")
        print("Use 'nori login <api-key>' to authenticate.")
        return 0

    try:
        user = api_request(repository, "/users/me", token=token)
    except ApiError as e:
        if e.status == 401:
            print(f"Token for {repository} is expired or revoked.")
            print("Use 'nori login <api-key>' to re-authenticate.")
            return 1
        print(f"Server error: {e.message}")
        return 1
    except ConnectionError as e:
        print(str(e))
        return 1

    use_ansi = sys.stdout.isatty()
    if use_ansi:
        BOLD, DIM, RESET = "\x1b[1m", "\x1b[2m", "\x1b[0m"
    else:
        BOLD, DIM, RESET = "", "", ""

    username = user.get("username", "unknown")
    email = user.get("email", "")
    packages = user.get("packages", [])

    print(f"{BOLD}Repository:{RESET}  {repository}")
    print(f"{BOLD}Username:{RESET}    {username}")
    if email:
        print(f"{BOLD}Email:{RESET}       {email}")

    if packages:
        print(f"\n{BOLD}Published packages ({len(packages)}):{RESET}")
        for pkg in sorted(packages):
            print(f"  {pkg}")
    else:
        print(f"\n{DIM}No published packages.{RESET}")

    return 0
