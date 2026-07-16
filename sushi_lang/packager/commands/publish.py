"""nori publish - publish a package to an Omakase repository."""
import argparse
import hashlib
import platform
from pathlib import Path

from sushi_lang.packager.api_client import api_upload_multipart, ApiError
from sushi_lang.packager.constants import MANIFEST_NAME
from sushi_lang.packager.credentials import load_token
from sushi_lang.packager.manifest import load_manifest
from sushi_lang.packager.repository import resolve_repository


def _detect_platform() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "darwin"
    if system == "linux":
        return "linux"
    return "any"


def cmd_publish(args: argparse.Namespace) -> int:
    repository = resolve_repository(args)
    ns = getattr(args, "namespace", "stable")
    plat = getattr(args, "platform", None) or _detect_platform()

    # Require nori.toml
    manifest_path = Path.cwd() / MANIFEST_NAME
    if not manifest_path.exists():
        print(f"No {MANIFEST_NAME} found in current directory.")
        print("Run 'nori publish' from a project directory with a nori.toml.")
        return 1

    manifest = load_manifest()

    # Locate built archive
    archive_path = Path.cwd() / "dist" / f"{manifest.archive_name}.nori"
    if not archive_path.exists():
        print(f"Archive not found: {archive_path}")
        print("Run 'nori build' first to create the package archive.")
        return 1

    # Require authentication
    token = load_token(repository)
    if not token:
        print(f"Not logged in to {repository}.")
        print("Use 'nori login <api-key>' to authenticate first.")
        return 1

    # Read archive and compute SHA-256
    archive_data = archive_path.read_bytes()
    sha256 = hashlib.sha256(archive_data).hexdigest()

    # Build manifest content with [publish] section
    manifest_content = manifest_path.read_text()
    manifest_content += "\n[publish]\n"
    manifest_content += f'namespace = "{ns}"\n'
    manifest_content += f'platform = "{plat}"\n'

    # Upload
    parts = [
        ("manifest", MANIFEST_NAME, "application/toml", manifest_content.encode()),
        ("archive", f"{manifest.archive_name}.nori", "application/octet-stream", archive_data),
    ]

    print(f"Publishing {manifest.name} v{manifest.version} to {repository}...")
    print(f"  Namespace: {ns}, Platform: {plat}")
    print(f"  Archive: {archive_path.name} ({len(archive_data)} bytes)")

    try:
        result = api_upload_multipart(
            repository,
            f"/packages/{manifest.name}/{manifest.version}/publish",
            token,
            parts,
            extra_headers={"X-Sha256": sha256},
        )
    except ApiError as e:
        if e.status == 401:
            print("Authentication failed. Token may be expired or revoked.")
            print("Use 'nori login <api-key>' to re-authenticate.")
            return 1
        if e.status == 403:
            print(f"Permission denied. You are not the owner of '{manifest.name}'.")
            return 1
        if e.status == 409:
            print(f"Version {manifest.version} already exists for '{manifest.name}'.")
            return 1
        if e.status == 413:
            print("Archive exceeds the maximum size limit (50 MB).")
            return 1
        if e.status == 422:
            print(f"Validation error: {e.message}")
            return 1
        print(f"Server error: {e.message}")
        return 1
    except ConnectionError as e:
        print(str(e))
        return 1

    published_at = result.get("published_at", "")
    print(f"Published {manifest.name} v{manifest.version}")
    if published_at:
        print(f"  Published at: {published_at}")
    return 0
