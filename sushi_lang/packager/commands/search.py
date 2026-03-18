"""nori search - search for packages in the repository."""
import argparse
import json
import sys
import urllib.request
import urllib.error
import urllib.parse

from sushi_lang.packager.repository import resolve_repository


def _format_downloads(count: int) -> str:
    if count > 999999:
        return "+999999"
    return str(count)


def _print_results(packages: list, use_ansi: bool) -> None:
    if use_ansi:
        BOLD = "\x1b[1m"
        DIM = "\x1b[2m"
        CYAN = "\x1b[1;36m"
        GREEN = "\x1b[32m"
        YELLOW = "\x1b[33m"
        RESET = "\x1b[0m"
    else:
        BOLD = DIM = CYAN = GREEN = YELLOW = RESET = ""

    # Compute dynamic column widths
    name_w = max(len(p.get("name", "")) for p in packages)
    name_w = max(name_w, 4)  # min width for "Name"
    ver_w = max(len(f"v{p.get('latest_version') or '---'}") for p in packages)
    ver_w = max(ver_w, 7)  # min width for "Version"
    lic_w = 12  # fixed
    dl_w = 9   # fixed, "Downloads"

    # Header
    header = (
        f"  {'Name':<{name_w}}  {'Version':<{ver_w}}  "
        f"{'License':<{lic_w}}  {'Downloads':>{dl_w}}  Description"
    )
    print(f"{BOLD}{header}{RESET}")

    # Separator
    if use_ansi:
        sep = (
            f"  {'\u2500' * name_w}  {'\u2500' * ver_w}  "
            f"{'\u2500' * lic_w}  {'\u2500' * dl_w}  {'\u2500' * 11}"
        )
        print(f"{DIM}{sep}{RESET}")

    # Rows
    for pkg in packages:
        name = pkg.get("name", "")
        version = f"v{pkg.get('latest_version') or '---'}"
        license_ = pkg.get("license", "")
        downloads = _format_downloads(pkg.get("total_downloads", 0))
        description = pkg.get("description", "")

        row = (
            f"  {CYAN}{name:<{name_w}}{RESET}  "
            f"{GREEN}{version:<{ver_w}}{RESET}  "
            f"{DIM}{license_:<{lic_w}}{RESET}  "
            f"{YELLOW}{downloads:>{dl_w}}{RESET}  "
            f"{description}"
        )
        print(row)


def cmd_search(args: argparse.Namespace) -> int:
    repository = resolve_repository(args)
    query = args.query

    params = {"q": query}
    if args.namespace:
        params["namespace"] = args.namespace
    if args.platform:
        params["platform"] = args.platform
    if args.sort:
        params["sort"] = args.sort
    if args.page != 1:
        params["page"] = args.page
    if args.per_page != 20:
        params["per_page"] = args.per_page

    url = f"https://{repository}/api/v1/packages?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "nori/1.0",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Repository error: HTTP {e.code}")
        return 1
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        print(f"Could not connect to {repository}: {reason}")
        return 1

    packages = data.get("packages", [])
    if not packages:
        print(f'No packages found for "{query}".')
        return 0

    _print_results(packages, sys.stdout.isatty())

    pagination = data.get("pagination", {})
    total = pagination.get("total", len(packages))
    page = pagination.get("page", 1)
    per_page = pagination.get("per_page", 20)
    total_pages = (total + per_page - 1) // per_page if per_page else 1
    print(f"\n  {total} package(s) found (page {page}/{total_pages})")
    return 0
