"""nori search - search for packages in the repository."""
import argparse
import sys
import urllib.parse

from sushi_lang.internals.styling import Palette, should_colour
from sushi_lang.packager.api_client import api_request
from sushi_lang.packager.repository import resolve_repository


def _format_downloads(count: int) -> str:
    if count > 999999:
        return "+999999"
    return str(count)


def _print_results(packages: list) -> None:
    p = Palette(should_colour(sys.stdout))

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
    print(f"{p.bold}{header}{p.reset}")

    sep = (
        f"  {'\u2500' * name_w}  {'\u2500' * ver_w}  "
        f"{'\u2500' * lic_w}  {'\u2500' * dl_w}  {'\u2500' * 11}"
    )
    print(f"{p.dim}{sep}{p.reset}")

    # Rows
    for pkg in packages:
        name = pkg.get("name", "")
        version = f"v{pkg.get('latest_version') or '---'}"
        license_ = pkg.get("license", "")
        downloads = _format_downloads(pkg.get("total_downloads", 0))
        description = pkg.get("description", "")

        row = (
            f"  {p.bold}{p.cyan}{name:<{name_w}}{p.reset}  "
            f"{p.green}{version:<{ver_w}}{p.reset}  "
            f"{p.dim}{license_:<{lic_w}}{p.reset}  "
            f"{p.yellow}{downloads:>{dl_w}}{p.reset}  "
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

    data = api_request(repository, f"/packages?{urllib.parse.urlencode(params)}")

    packages = data.get("packages", [])
    if not packages:
        print(f'No packages found for "{query}".')
        return 0

    _print_results(packages)

    pagination = data.get("pagination", {})
    total = pagination.get("total", len(packages))
    page = pagination.get("page", 1)
    per_page = pagination.get("per_page", 20)
    total_pages = (total + per_page - 1) // per_page if per_page else 1
    print(f"\n  {total} package(s) found (page {page}/{total_pages})")
    return 0
