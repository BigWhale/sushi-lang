"""nori status - show login status and published packages."""
import argparse
import sys

from sushi_lang.internals.styling import Palette, should_colour
from sushi_lang.packager.api_client import api_request, ApiError
from sushi_lang.packager.credentials import load_token
from sushi_lang.packager.errors import RELOGIN_HELP, NoriError
from sushi_lang.packager.repository import resolve_repository


def cmd_status(args: argparse.Namespace) -> int:
    repository = resolve_repository(args)
    token = load_token(repository)

    if not token:
        print(f"Not logged in to {repository}.")
        print("Use 'nori login' to authenticate.")
        return 0

    try:
        user = api_request(repository, "/users/me", token=token)
    except ApiError as e:
        if e.status == 401:
            raise NoriError("NE5004", repository=repository, helps=[RELOGIN_HELP]) from e
        raise

    p = Palette(should_colour(sys.stdout))
    BOLD, DIM, RESET = p.bold, p.dim, p.reset

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
