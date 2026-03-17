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

    url = f"https://{repository}/api/v1/packages?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
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
        description = pkg.get("description", "")
        if len(description) > 60:
            description = description[:57] + "..."
        print(f"  {name} v{version}    {description}")

    total = data.get("pagination", {}).get("total", len(packages))
    print(f"\n  {total} package(s) found")
    return 0
