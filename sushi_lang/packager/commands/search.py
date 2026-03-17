"""nori search - search for packages in the repository."""
import argparse
import json
import urllib.request
import urllib.error
import urllib.parse

from sushi_lang.packager.repository import resolve_repository


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

    for pkg in packages:
        name = pkg.get("name", "")
        version = pkg.get("latest_version") or "---"
        license_ = pkg.get("license", "")
        downloads = pkg.get("total_downloads", 0)
        description = pkg.get("description", "")
        if len(description) > 50:
            description = description[:47] + "..."
        parts = [f"  {name} v{version}"]
        if license_:
            parts.append(license_)
        parts.append(f"{downloads} downloads")
        parts.append(description)
        print("  ".join(parts))

    pagination = data.get("pagination", {})
    total = pagination.get("total", len(packages))
    page = pagination.get("page", 1)
    per_page = pagination.get("per_page", 20)
    total_pages = (total + per_page - 1) // per_page if per_page else 1
    print(f"\n  {total} package(s) found (page {page}/{total_pages})")
    return 0
